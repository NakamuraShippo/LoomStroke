"""GPT Image 2 AI service plugin."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from loomstroke.config.secret_provider import SecretNotConfigured, SecretProvider
from loomstroke.platform import get_settings_manager
from loomstroke.platform.user_dirs import plugin_data_dir
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent
from loomstroke.plugins.sdk.base import (
    JobResult,
    JobSpec,
    PluginMetadata,
    PluginType,
    ProgressEvent,
    ServiceDescriptor,
)

from .input_images import MAX_INPUT_LAYERS, normalize_input_pngs
from .key_settings import (
    GPTIMAGE2_API_KEY_SERVICE,
    GPTIMAGE2_API_KEY_USERNAME,
    GPTIMAGE2_USE_OPENAI_API_KEY_SETTING,
    OPENAI_API_KEY_SERVICE,
    OPENAI_API_KEY_USERNAME,
)
from .transport import IGPTImage2Transport, OpenAIImageTransport
from .types import GPTImage2Params

logger = logging.getLogger(__name__)


@dataclass
class _JobState:
    job_id: str
    params: GPTImage2Params
    apply_to_layer: bool
    input_pngs: list[bytes]
    queue: asyncio.Queue[ProgressEvent]
    cancel_event: asyncio.Event
    task: asyncio.Task[None] | None = None
    result: JobResult | None = None


_SERVICE_SINGLETON: GPTImage2Service | None = None


class GPTImage2Service:
    """OpenAI GPT Image 2 powered image generation service."""

    metadata = PluginMetadata(
        id="gptimage2_service",
        name="GPT Image 2 AI Service",
        version="1.0.0",
        description="OpenAI GPT Image 2 image generation",
        author="LoomStroke Team",
        types=[PluginType.AI_SERVICE],
    )

    def __init__(
        self,
        transport: IGPTImage2Transport | None = None,
        *,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        self._transport = transport or OpenAIImageTransport()
        self._descriptor = ServiceDescriptor(
            name="ai.gptimage2",
            capabilities={"text2image", "image_guided"},
            max_concurrency=1,
            max_resolution=(3840, 3840),
            supports_streaming=True,
            requires_api_key=True,
        )
        self._jobs: dict[str, _JobState] = {}
        self._host: Any = None
        self._secrets: SecretProvider = secret_provider or SecretProvider()
        self._data_root = plugin_data_dir("gptimage2")

    def descriptor(self) -> ServiceDescriptor:
        return self._descriptor

    async def health(self) -> bool:
        if not ensure_network_services_consent(self._host, service_name="GPT Image 2"):
            return False
        return await self._transport.health()

    async def submit(self, spec: JobSpec) -> str:
        if not ensure_network_services_consent(self._host, service_name="GPT Image 2"):
            raise RuntimeError(
                "GPT Image 2 access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )
        if spec.op != "generate":
            raise ValueError(f"Unsupported GPT Image 2 op: {spec.op}")
        if not isinstance(spec.params, Mapping):
            raise ValueError("JobSpec.params must be a mapping")
        params = GPTImage2Params.from_mapping(spec.params)
        if not params.prompt:
            raise ValueError("Prompt is required")
        input_pngs = normalize_input_pngs(spec.params.get("input_pngs", []))
        if len(input_pngs) + len(params.input_layer_ids) > MAX_INPUT_LAYERS:
            raise ValueError(f"At most {MAX_INPUT_LAYERS} input images are supported")
        apply_to_layer = bool(spec.params.get("apply_to_layer", True))

        job_id = uuid.uuid4().hex
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()
        cancel_event = asyncio.Event()
        state = _JobState(
            job_id=job_id,
            params=params,
            apply_to_layer=apply_to_layer,
            input_pngs=input_pngs,
            queue=queue,
            cancel_event=cancel_event,
        )
        self._jobs[job_id] = state

        await queue.put(ProgressEvent(job_id=job_id, step=0, total=4, message="queued"))

        async def _task() -> None:
            await self._run_job(state)

        state.task = asyncio.create_task(_task())
        return job_id

    async def stream(self, job_id: str) -> AsyncIterator[ProgressEvent]:
        if not ensure_network_services_consent(self._host, service_name="GPT Image 2"):
            raise RuntimeError(
                "GPT Image 2 access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )
        state = self._require_state(job_id)
        queue = state.queue
        while True:
            event = await queue.get()
            yield event
            if event.step >= event.total:
                break

    async def cancel(self, job_id: str) -> bool:
        if not ensure_network_services_consent(self._host, service_name="GPT Image 2"):
            return False
        state = self._jobs.get(job_id)
        if not state:
            return False
        state.cancel_event.set()
        task = state.task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return True

    async def result(self, job_id: str) -> JobResult:
        if not ensure_network_services_consent(self._host, service_name="GPT Image 2"):
            raise RuntimeError(
                "GPT Image 2 access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )
        state = self._require_state(job_id)
        task = state.task
        if task and not task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if state.result is None:
            raise ValueError(f"Job {job_id} has no result yet")
        result = state.result
        if job_id in self._jobs and (state.task is None or state.task.done()):
            self._jobs.pop(job_id, None)
        return result

    def activate(self, host_api: Any) -> None:  # type: ignore[override]
        self._host = host_api
        if hasattr(self._transport, "attach_host"):
            self._transport.attach_host(host_api)  # type: ignore[arg-type]

    def deactivate(self) -> None:  # type: ignore[override]
        for state in list(self._jobs.values()):
            if state.task and not state.task.done():
                state.task.cancel()
        self._jobs.clear()
        if hasattr(self._transport, "detach_host"):
            self._transport.detach_host()
        self._host = None

    def _require_state(self, job_id: str) -> _JobState:
        state = self._jobs.get(job_id)
        if not state:
            raise KeyError(f"unknown job_id: {job_id}")
        return state

    def is_api_key_configured(self) -> bool:
        service_name, username = self._api_key_ref()
        return self._secrets.has(service_name, username=username)

    def _secret_provider(self) -> SecretProvider:
        return self._secrets

    def _use_openai_api_key(self) -> bool:
        value = self._settings_get(GPTIMAGE2_USE_OPENAI_API_KEY_SETTING, False)
        return bool(value)

    def _settings_get(self, key: str, default: Any = None) -> Any:
        settings = getattr(self._host, "settings", None)
        getter = getattr(settings, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception:
                logger.debug("Failed to read GPT Image 2 host setting %s", key, exc_info=True)
        try:
            return get_settings_manager().get(key, default)
        except Exception:
            logger.debug("Failed to read GPT Image 2 global setting %s", key, exc_info=True)
            return default

    def _api_key_ref(self) -> tuple[str, str]:
        if self._use_openai_api_key():
            return OPENAI_API_KEY_SERVICE, OPENAI_API_KEY_USERNAME
        return GPTIMAGE2_API_KEY_SERVICE, GPTIMAGE2_API_KEY_USERNAME

    def _api_key_display_name(self) -> str:
        return "OpenAI" if self._use_openai_api_key() else "GPT Image 2"

    def api_key_ref(self) -> tuple[str, str]:
        return self._api_key_ref()

    def api_key_display_name(self) -> str:
        return self._api_key_display_name()

    async def _prepare_input_images(self, state: _JobState) -> list[bytes]:
        input_pngs = list(state.input_pngs)
        for layer_id in state.params.input_layer_ids:
            if state.cancel_event.is_set():
                raise asyncio.CancelledError()
            input_pngs.append(await self._transport.export_layer_png(layer_id))
        if state.cancel_event.is_set():
            raise asyncio.CancelledError()
        return normalize_input_pngs(input_pngs)

    async def _run_job(self, state: _JobState) -> None:
        job_id = state.job_id
        queue = state.queue
        cancel_event = state.cancel_event
        params = state.params
        loop = asyncio.get_running_loop()

        def _emit_progress(message: str) -> None:
            try:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ProgressEvent(job_id=job_id, step=2, total=4, message=message),
                )
            except RuntimeError:
                logger.debug("Event loop closed before progress could be emitted")

        try:
            await queue.put(ProgressEvent(job_id=job_id, step=1, total=4, message="preparing"))
            if not ensure_network_services_consent(self._host, service_name="GPT Image 2"):
                raise RuntimeError("GPT Image 2 access was disabled while processing the job.")

            input_pngs = await self._prepare_input_images(state)
            if not ensure_network_services_consent(self._host, service_name="GPT Image 2"):
                raise RuntimeError("GPT Image 2 access was disabled while processing the job.")

            await queue.put(ProgressEvent(job_id=job_id, step=2, total=4, message="running"))
            secrets = self._secret_provider()
            service_name, username = self._api_key_ref()
            try:
                with secrets.use(service_name, username=username) as api_key:
                    input_kwargs: dict[str, Any] = {"input_pngs": input_pngs} if input_pngs else {}
                    image_bytes = await self._transport.generate(
                        prompt=params.prompt,
                        options=params.options,
                        emit_progress=_emit_progress,
                        cancelled=cancel_event.is_set,
                        api_key=api_key,
                        **input_kwargs,
                    )
            except SecretNotConfigured as exc:
                raise RuntimeError(
                    f"{self._api_key_display_name()} API key is not configured. Set it in "
                    "Preferences -> AI Effect -> API Keys before generating."
                ) from exc

            if cancel_event.is_set():
                raise asyncio.CancelledError()

            await queue.put(
                ProgressEvent(
                    job_id=job_id,
                    step=3,
                    total=4,
                    message="applying" if state.apply_to_layer else "finalizing",
                    preview_png=image_bytes,
                )
            )

            metadata = {
                "model": params.options.model,
                "size": params.options.size,
                "quality": params.options.quality,
                "output_format": params.options.output_format,
                "output_compression": params.options.output_compression,
                "background": params.options.background,
                "moderation": params.options.moderation,
                "prompt_length": len(params.prompt),
                "input_count": len(input_pngs),
            }
            if state.apply_to_layer:
                await self._transport.apply_image_to_layer(
                    image_bytes,
                    target_layer_id=params.options.target_layer_id,
                    metadata=metadata,
                )

            state.result = JobResult(
                job_id=job_id,
                success=True,
                output_type="image",
                output=image_bytes,
                metadata=metadata,
            )
            await queue.put(ProgressEvent(job_id=job_id, step=4, total=4, message="completed"))
        except asyncio.CancelledError:
            state.result = JobResult(job_id=job_id, success=False, error="cancelled")
            await queue.put(ProgressEvent(job_id=job_id, step=4, total=4, message="cancelled"))
        except Exception as exc:
            logger.exception("GPT Image 2 job %s failed", job_id)
            state.result = JobResult(job_id=job_id, success=False, error=str(exc))
            await queue.put(ProgressEvent(job_id=job_id, step=4, total=4, message=f"error: {exc}"))
        finally:
            state.task = None


def get_service_instance() -> GPTImage2Service:
    global _SERVICE_SINGLETON  # noqa: PLW0603
    if _SERVICE_SINGLETON is None:
        _SERVICE_SINGLETON = GPTImage2Service()
    return _SERVICE_SINGLETON


def create_plugin() -> GPTImage2Service:
    """Factory used by the plugin registry."""
    return get_service_instance()
