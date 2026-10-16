"""NanoBanana AI service plugin."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from loomstroke.config.secret_provider import SecretNotConfigured, SecretProvider
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

from .transport import GoogleGenAITransport, INanoBananaTransport
from .types import MAX_INPUT_LAYERS, NanoBananaParams

logger = logging.getLogger(__name__)
_SECURE_API_KEY_SERVICE = "nanobanana"


def _extract_runtime_input_pngs(payload: Mapping[str, Any]) -> list[bytes]:
    """Return AIEffect-provided input PNG bytes from an in-memory JobSpec."""
    raw = payload.get("input_pngs", [])
    if raw in (None, "", b""):
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ValueError("input_pngs must be a sequence of bytes")
    input_pngs: list[bytes] = []
    for value in raw:
        if value is None:
            continue
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("input_pngs must contain bytes")
        data = bytes(value)
        if data:
            input_pngs.append(data)
        if len(input_pngs) >= MAX_INPUT_LAYERS:
            break
    return input_pngs


@dataclass
class _JobState:
    job_id: str
    params: NanoBananaParams
    input_pngs: list[bytes]
    queue: asyncio.Queue[ProgressEvent]
    cancel_event: asyncio.Event
    task: asyncio.Task[None] | None = None
    result: JobResult | None = None


_SERVICE_SINGLETON: NanoBananaService | None = None


class NanoBananaService:
    """Google Gemini powered image generation service."""

    metadata = PluginMetadata(
        id="nanobanana_service",
        name="NanoBanana AI Service",
        version="2.0.0",
        description="Gemini-based NanoBanana image generation",
        author="LoomStroke Team",
        types=[PluginType.AI_SERVICE],
    )

    def __init__(
        self,
        transport: INanoBananaTransport | None = None,
        *,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        self._transport = transport or GoogleGenAITransport()
        self._descriptor = ServiceDescriptor(
            name="ai.nanobanana",
            capabilities={"text2image", "image_guided", "search_grounding"},
            max_concurrency=2,
            max_resolution=(3072, 3072),
            supports_streaming=True,
            requires_api_key=True,
        )
        self._jobs: dict[str, _JobState] = {}
        self._host: Any = None
        # Phase 8a: read-on-demand secret provider injected at app
        # bootstrap. Defaults to a fresh ``SecretProvider`` so callers
        # that build the service directly still use the keyring path.
        self._secrets: SecretProvider = secret_provider or SecretProvider()
        # Ensure plugin-local data root exists under user_data/
        self._data_root = plugin_data_dir("nanobanana")

    # ------------------------------------------------------------------
    def descriptor(self) -> ServiceDescriptor:
        return self._descriptor

    async def health(self) -> bool:
        if not ensure_network_services_consent(self._host, service_name="NanoBanana"):
            return False
        return await self._transport.health()

    async def submit(self, spec: JobSpec) -> str:
        if not ensure_network_services_consent(self._host, service_name="NanoBanana"):
            raise RuntimeError(
                "NanoBanana access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )
        if spec.op != "generate":
            raise ValueError(f"Unsupported NanoBanana op: {spec.op}")
        if not isinstance(spec.params, Mapping):
            raise ValueError("JobSpec.params must be a mapping")
        params = NanoBananaParams.from_mapping(spec.params)
        input_pngs = _extract_runtime_input_pngs(spec.params)
        if not params.prompt:
            raise ValueError("Prompt is required")
        if len(params.input_layer_ids) + len(input_pngs) > MAX_INPUT_LAYERS:
            raise ValueError(f"NanoBanana inputs must contain at most {MAX_INPUT_LAYERS} items")

        job_id = uuid.uuid4().hex
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()
        cancel_event = asyncio.Event()
        state = _JobState(
            job_id=job_id,
            params=params,
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
        if not ensure_network_services_consent(self._host, service_name="NanoBanana"):
            raise RuntimeError(
                "NanoBanana access is disabled. "
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
        if not ensure_network_services_consent(self._host, service_name="NanoBanana"):
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
        if not ensure_network_services_consent(self._host, service_name="NanoBanana"):
            raise RuntimeError(
                "NanoBanana access is disabled. "
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

    # ------------------------------------------------------------------
    def activate(self, host_api: Any) -> None:  # type: ignore[override]  # plugin API signature divergence
        self._host = host_api
        if hasattr(self._transport, "attach_host"):
            self._transport.attach_host(host_api)  # type: ignore[arg-type]  # duck-typed transport protocol
        # Phase 8a "read-on-demand": API key is NEVER pulled into
        # transport memory at activation. ``_run_job`` reads the key
        # only when the user clicks Generate, hands it to
        # ``transport.generate(api_key=...)``, and lets the local
        # binding fall out of scope on return.

    def deactivate(self) -> None:  # type: ignore[override]  # plugin API signature divergence
        for state in list(self._jobs.values()):
            if state.task and not state.task.done():
                state.task.cancel()
        self._jobs.clear()
        if hasattr(self._transport, "detach_host"):
            self._transport.detach_host()
        self._host = None

    # ------------------------------------------------------------------
    def _require_state(self, job_id: str) -> _JobState:
        state = self._jobs.get(job_id)
        if not state:
            raise KeyError(f"unknown job_id: {job_id}")
        return state

    def is_api_key_configured(self) -> bool:
        """Public read-on-demand existence check.

        Phase 8a: UI surfaces ("API key configured ✓") need bool-only
        knowledge. Routes through ``SecretProvider.has`` which uses
        ``SecurityConfig.has_credential`` (keyring metadata only,
        secret value never returned to caller).
        """
        return self._secrets.has(_SECURE_API_KEY_SERVICE)

    def _secret_provider(self) -> SecretProvider:
        """Return the injected ``SecretProvider``.

        Tests can swap it via the constructor kwarg; production wires
        a process-wide instance from app bootstrap so all Gateways
        share the same keyring entry point.
        """
        return self._secrets

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
            if not ensure_network_services_consent(self._host, service_name="NanoBanana"):
                raise RuntimeError("NanoBanana access was disabled while processing the job.")

            input_pngs: list[bytes] = list(state.input_pngs)
            for layer_id in params.input_layer_ids:
                if cancel_event.is_set():
                    raise asyncio.CancelledError()
                data = await self._transport.export_layer_png(layer_id)
                input_pngs.append(data)

            await queue.put(ProgressEvent(job_id=job_id, step=2, total=4, message="running"))

            # Phase 8a "no-app-side-key" policy: the key is fetched
            # by ``SecretProvider.use`` at the network-call boundary
            # only. Plugin code (this method's caller, the panel,
            # the AI Effect controller) never sees the value — it
            # exists only inside the ``with`` block below.
            secrets = self._secret_provider()
            try:
                with secrets.use(_SECURE_API_KEY_SERVICE) as api_key:
                    result_png = await self._transport.generate(
                        prompt=params.prompt,
                        input_pngs=input_pngs,
                        options=params.options,
                        emit_progress=_emit_progress,
                        cancelled=cancel_event.is_set,
                        api_key=api_key,
                    )
            except SecretNotConfigured as exc:
                raise RuntimeError(
                    "NanoBanana API key is not configured. Set it in "
                    "Preferences → AI Effect → API Keys before generating."
                ) from exc

            if cancel_event.is_set():
                raise asyncio.CancelledError()

            await queue.put(
                ProgressEvent(
                    job_id=job_id,
                    step=3,
                    total=4,
                    message="applying",
                    preview_png=result_png,
                )
            )

            metadata = {
                "aspect_ratio": params.options.aspect_ratio,
                "modality": params.options.modality,
                "model": params.options.model,
                "image_size": params.options.image_size,
                "thinking_level": params.options.thinking_level,
                "include_thoughts": params.options.include_thoughts,
                "google_search": params.options.google_search,
                "prompt_length": len(params.prompt),
                "input_layers": params.input_layer_ids,
            }
            await self._transport.apply_png_to_layer(
                result_png,
                target_layer_id=params.options.target_layer_id,
                metadata=metadata,
            )

            result = JobResult(
                job_id=job_id,
                success=True,
                output_type="image",
                output=result_png,
                metadata=metadata,
            )
            state.result = result
            await queue.put(ProgressEvent(job_id=job_id, step=4, total=4, message="completed"))
        except asyncio.CancelledError:
            state.result = JobResult(job_id=job_id, success=False, error="cancelled")
            await queue.put(ProgressEvent(job_id=job_id, step=4, total=4, message="cancelled"))
        except Exception as exc:
            logger.exception("NanoBanana job %s failed", job_id)
            state.result = JobResult(job_id=job_id, success=False, error=str(exc))
            await queue.put(ProgressEvent(job_id=job_id, step=4, total=4, message=f"error: {exc}"))
        finally:
            state.task = None


def get_service_instance() -> NanoBananaService:
    global _SERVICE_SINGLETON  # noqa: PLW0603
    if _SERVICE_SINGLETON is None:
        _SERVICE_SINGLETON = NanoBananaService()
    return _SERVICE_SINGLETON


def create_plugin() -> NanoBananaService:
    """Factory used by the plugin registry."""
    return get_service_instance()
