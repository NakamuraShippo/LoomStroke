# ruff: noqa: I001

"""Client wrapper around the llm_gateway_service plugin."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import asdict
from typing import Any

from loomstroke.host_api import HostAPI, SettingsService
from loomstroke.plugins.llm_gateway.config import (
    LLMConfig,
    available_api_key_providers,
)
from loomstroke.plugins.llm_gateway.config import load as load_llm_config
from loomstroke.plugins.llm_gateway.config import (
    resolve_model,
)
from loomstroke.plugins.llm_gateway.errors import AttachmentError
from loomstroke.plugins.llm_gateway.types import LayerSelectionRequest
from loomstroke.plugins.sdk.base import AIServicePlugin, JobResult, ProgressEvent
from loomstroke.plugins.sdk.registry import PluginRegistry

from .snippet_models import Attachment, PromptPayload

logger = logging.getLogger(__name__)


class LLMGatewayClient:
    """Thin adapter used by the TextEditor panel to reach the gateway service."""

    _service_id = "llm_gateway_service"

    def __init__(
        self,
        host: HostAPI | None,
        settings: SettingsService | None,
        registry: PluginRegistry | None = None,
    ) -> None:

        self._host = host

        self._settings = settings

        self._registry: PluginRegistry | None = registry

        if self._registry is None:

            self._registry = self._detect_registry()

        self._service: AIServicePlugin | None = None

        self._config: LLMConfig | None = None

    # ---- service access ---------------------------------------------------

    def service(self) -> AIServicePlugin:

        service = self._service

        if service is not None:

            return service

        registry = self._registry or self._detect_registry()

        if registry is None:

            raise RuntimeError("Plugin registry unavailable; cannot reach LLM gateway")

        try:

            plugin = registry.get(self._service_id)

        except KeyError as exc:

            raise RuntimeError("LLM gateway service is not registered") from exc

        if not isinstance(plugin, AIServicePlugin):

            raise RuntimeError("Registered LLM gateway is not an AIServicePlugin")

        # Ensure plugin is activated with the host API when possible

        if hasattr(plugin, "activate") and getattr(plugin, "_host", None) is None and self._host:

            try:

                plugin.activate(self._host)

            except Exception:

                logger.exception("LLM gateway activation failed")

        self._service = plugin

        return plugin

    def descriptor(self) -> Any:

        with suppress(Exception):

            return self.service().descriptor()

        return None

    def config(self) -> LLMConfig:

        if self._config is not None:

            return self._config

        cfg = load_llm_config(self._settings_object())

        self._config = cfg

        return cfg

    def providers(self) -> list[str]:

        cfg = self.config()

        providers = {cfg.provider}

        providers.update(cfg.models.keys())

        providers.update(available_api_key_providers())

        return sorted(provider for provider in providers if provider)

    def default_model(self, provider: str) -> str:

        cfg = self.config()

        return resolve_model(cfg, provider, None)

    def models_for(self, provider: str) -> list[str]:
        """Return the selectable models for *provider*.

        Combines the curated catalogue (``known_models``) with the
        user's currently-configured model so the dropdown lists every
        first-party option AND respects a user-pasted custom model
        ID. Order: configured model first (so it's the default
        selection), then the curated list, deduplicated.
        """
        from loomstroke.plugins.llm_gateway.config import (  # noqa: PLC0415
            known_models,
        )

        cfg = self.config()
        provider_key = provider.lower()
        catalogue = known_models(provider_key)
        configured = cfg.models.get(provider_key) or resolve_model(cfg, provider_key, None)
        ordered: list[str] = []
        if configured:
            ordered.append(configured)
        for model in catalogue:
            if model and model not in ordered:
                ordered.append(model)
        return ordered or [provider_key]

    # ---- submission -------------------------------------------------------

    async def submit(self, payload: PromptPayload) -> str:

        spec = payload.to_job_spec()

        logger.info("Submitting prompt via LLM gateway: %s", payload.provider)

        return await self.service().submit(spec)

    async def stream(self, job_id: str) -> AsyncIterator[ProgressEvent]:

        async for event in self.service().stream(job_id):

            yield event

    async def result(self, job_id: str) -> JobResult:

        return await self.service().result(job_id)

    async def cancel(self, job_id: str) -> None:

        with suppress(Exception):

            await self.service().cancel(job_id)  # type: ignore[arg-type]  # Optional[str] narrowed by suppress context

    # ---- attachments ------------------------------------------------------

    async def attach_layer(self, layer_id: str | None = None) -> Attachment:

        service = self.service()

        getter = getattr(service, "layer_queue", None)

        if not callable(getter):

            raise RuntimeError("LLM gateway layer queue unavailable")

        queue = getter()

        if queue is None:

            raise RuntimeError("Layer queue not initialized")

        loop = asyncio.get_running_loop()

        future: asyncio.Future[Any] = loop.create_future()

        async def _callback(result, error) -> None:

            if future.done():

                return

            if error:

                future.set_exception(error)

            else:

                future.set_result(result)

        request = LayerSelectionRequest(layer_id=layer_id)

        try:

            await queue.enqueue(request, _callback)

        except AttachmentError as exc:

            raise RuntimeError(str(exc)) from exc

        snapshot = await future

        attachment = Attachment(
            id=snapshot.request_id,
            source=snapshot.source,
            mime=snapshot.mime,
            data=snapshot.data,
            size_bytes=len(snapshot.data),
            width=snapshot.width,
            height=snapshot.height,
            layer=asdict(snapshot.layer) if snapshot.layer else None,
        )

        return attachment

    # ---- helpers ----------------------------------------------------------

    def _settings_object(self) -> Any:

        candidate = self._settings

        if candidate is None:

            return None

        # SettingsService exposes get/set, but load_llm_config also accepts QSettings

        if hasattr(candidate, "settings"):

            inner = candidate.settings

            if inner is not None:

                return inner

        return candidate

    def _detect_registry(self) -> PluginRegistry | None:

        current = getattr(self, "_registry", None)

        if current:

            return current

        host = self._host

        if not host:

            return None

        attr = getattr(host, "plugin_registry", None)

        if isinstance(attr, PluginRegistry):

            self._registry = attr

            return attr

        ui = getattr(host, "ui", None)

        if ui and hasattr(ui, "get_main_window"):

            with suppress(Exception):

                main_window = ui.get_main_window()

            if main_window and hasattr(main_window, "plugin_registry"):

                registry = main_window.plugin_registry

                if isinstance(registry, PluginRegistry):

                    self._registry = registry

                    return registry

        return None


__all__ = ["LLMGatewayClient"]
