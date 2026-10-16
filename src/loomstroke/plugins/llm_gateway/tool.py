"""LLM gateway AIServicePlugin implementation."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent
from loomstroke.plugins.sdk.base import (
    AIServicePlugin,
    JobResult,
    JobSpec,
    PluginMetadata,
    PluginType,
    ProgressEvent,
    ServiceDescriptor,
)

from .adapters.anthropic import ClaudeAdapter
from .adapters.base import (
    CancelContext,
    HealthContext,
    ProviderAdapter,
    ProviderChunk,
    ProviderConfig,
    ProviderJob,
    StreamContext,
    SubmitContext,
)
from .adapters.gemini import GeminiAdapter
from .adapters.openai import OpenAIAdapter
from .config import (
    LLMConfig,
    SecretNotConfigured,
    load,
    use_api_key,
)
from .errors import InvalidRequestError
from .layer_tasks import LayerTaskQueue
from .prompt import build_json_schema, build_messages
from .session import SessionManager
from .types import ImageAttachment, LayerMetadata

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = {
    "openai": OpenAIAdapter,
    "anthropic": ClaudeAdapter,
    "gemini": GeminiAdapter,
}


@dataclass
class PendingJob:
    job_id: str
    provider: str
    adapter: ProviderAdapter
    provider_job: ProviderJob
    response_format: str = "text"
    buffer: list[str] = field(default_factory=list)
    usage: Mapping[str, Any] | None = None
    finished: bool = False
    error: str | None = None


class LLMGatewayPlugin(AIServicePlugin):
    """AIServicePlugin facade over multiple LLM providers."""

    metadata = PluginMetadata(
        id="llm_gateway_service",
        name="LLM Gateway",
        version="1.0.0",
        description="Unified LLM client for OpenAI, Anthropic, and Gemini",
        author="LoomStroke Team",
        license="MIT",
        types=[PluginType.AI_SERVICE],
    )

    def __init__(self) -> None:
        self._host: Any | None = None
        self._config: LLMConfig | None = None
        self._session_mgr = SessionManager()
        self._adapters: dict[str, ProviderAdapter] = {}
        self._pending: dict[str, PendingJob] = {}
        self._layer_queue: LayerTaskQueue | None = None
        self._descriptor = ServiceDescriptor(
            name="llm_gateway",
            capabilities={"text", "json", "image"},
            max_concurrency=2,
            supports_streaming=True,
            requires_api_key=True,
        )

    # ---- Lifecycle --------------------------------------------------
    def descriptor(self) -> ServiceDescriptor:
        config = self._ensure_config()
        return ServiceDescriptor(
            name=self._descriptor.name,
            capabilities=self._descriptor.capabilities,
            max_concurrency=config.max_concurrency,
            supports_streaming=True,
            requires_api_key=True,
        )

    async def health(self) -> bool:
        if not ensure_network_services_consent(self._host, service_name="LLM Gateway"):
            return False
        config = self._ensure_config()
        provider_key = config.provider
        adapter = self._require_adapter(provider_key)
        session = await self._session_mgr.ensure(
            timeout=config.timeout, verify_ssl=config.verify_ssl
        )
        try:
            with use_api_key(provider_key) as api_key:
                provider_config = self._build_provider_config(config, provider_key, None, api_key)
                ctx = HealthContext(session=session, config=provider_config)
                return await adapter.health(ctx)
        except SecretNotConfigured:
            # Health check is informational — report unhealthy rather
            # than raising so the UI just shows a "configure key" hint.
            return False

    async def submit(self, spec: JobSpec) -> str:
        if not ensure_network_services_consent(self._host, service_name="LLM Gateway"):
            raise RuntimeError(
                "LLM Gateway access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )
        if spec.op != "prompt_completion":
            raise InvalidRequestError(f"unsupported op: {spec.op}")
        config = self._ensure_config()
        provider_key = str(spec.params.get("provider", config.provider)).lower()
        adapter = self._require_adapter(provider_key)
        model_override = spec.params.get("model")
        try:
            with use_api_key(provider_key) as api_key:
                provider_config = self._build_provider_config(
                    config, provider_key, model_override, api_key
                )
                messages_raw = self._coerce_messages(spec.params.get("prompt"))
                system_text = spec.params.get("system")
                if not system_text:
                    system_text = config.system_prompts.get(provider_key, "")
                response_mode = str(spec.params.get("response_format", "text"))
                response_schema = (
                    spec.params.get("json_schema") if response_mode == "json" else None
                )
                response_format = build_json_schema(response_schema, provider_key)
                temperature = float(spec.params.get("temperature", 0.7))
                max_tokens = spec.params.get("max_tokens")
                attachments = self._normalize_attachments(spec.params.get("images", []))
                messages = build_messages(system_text, messages_raw)
                payload = adapter.build_payload(
                    messages=messages,
                    response_format=response_format,
                    attachments=attachments,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                session = await self._session_mgr.ensure(
                    timeout=config.timeout, verify_ssl=config.verify_ssl
                )
                job_id = uuid4().hex
                submit_ctx = SubmitContext(
                    session=session,
                    config=provider_config,
                    payload=payload,
                    attachments=attachments,
                    job_id=job_id,
                )
                provider_job = await adapter.submit(submit_ctx)
        except SecretNotConfigured as exc:
            raise InvalidRequestError(f"api key not configured for {provider_key}") from exc
        self._pending[job_id] = PendingJob(
            job_id=job_id,
            provider=provider_key,
            adapter=adapter,
            provider_job=provider_job,
            response_format=response_mode,
        )
        return job_id

    async def stream(self, job_id: str) -> AsyncIterator[ProgressEvent]:
        if not ensure_network_services_consent(self._host, service_name="LLM Gateway"):
            raise RuntimeError(
                "LLM Gateway access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )
        pending = self._pending.get(job_id)
        if not pending:
            raise InvalidRequestError(f"unknown job: {job_id}")
        config = self._ensure_config()
        session = await self._session_mgr.ensure(
            timeout=config.timeout, verify_ssl=config.verify_ssl
        )
        try:
            with use_api_key(pending.provider) as api_key:
                provider_config = self._build_provider_config(
                    config,
                    pending.provider,
                    pending.provider_job.model or None,
                    api_key,
                )
                stream_ctx = StreamContext(
                    session=session, config=provider_config, job=pending.provider_job
                )
                step = 0
                async for chunk in pending.adapter.stream(stream_ctx):
                    step += 1
                    self._absorb_chunk(pending, chunk)
                    yield ProgressEvent(
                        job_id=job_id,
                        step=step,
                        total=1,
                        message=chunk.delta or None,
                        metadata={"usage": dict(chunk.usage)} if chunk.usage else None,
                    )
                pending.finished = True
        except SecretNotConfigured as exc:
            raise InvalidRequestError(f"api key not configured for {pending.provider}") from exc

    async def cancel(self, job_id: str) -> bool:
        if not ensure_network_services_consent(self._host, service_name="LLM Gateway"):
            return False
        pending = self._pending.pop(job_id, None)
        if not pending:
            return False
        config = self._ensure_config()
        session = await self._session_mgr.ensure(
            timeout=config.timeout, verify_ssl=config.verify_ssl
        )
        try:
            with use_api_key(pending.provider) as api_key:
                provider_config = self._build_provider_config(
                    config,
                    pending.provider,
                    pending.provider_job.model or None,
                    api_key,
                )
                cancel_ctx = CancelContext(
                    session=session, config=provider_config, job=pending.provider_job
                )
                await pending.adapter.cancel(cancel_ctx)
                return True
        except SecretNotConfigured:
            # Best-effort cancel: if the key is gone the remote job
            # cannot be addressed anyway. Treat as a no-op cancel.
            return False

    async def result(self, job_id: str) -> JobResult:
        if not ensure_network_services_consent(self._host, service_name="LLM Gateway"):
            return JobResult(
                job_id=job_id,
                success=False,
                error="LLM Gateway access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable.",
            )
        pending = self._pending.get(job_id)
        if not pending:
            return JobResult(
                job_id=job_id, success=False, error="job not found", output_type="text"
            )
        if not pending.finished:
            async for _ in self.stream(job_id):
                break
        text = "".join(pending.buffer)
        data: dict[str, Any] = {"text": text}
        if pending.usage:
            data["usage"] = dict(pending.usage)
        output_type = "json" if pending.response_format == "json" else "text"
        return JobResult(
            job_id=job_id,
            success=pending.error is None,
            output_type=output_type,
            data=data,
            error=pending.error,
        )

    def activate(self, host_api: Any) -> None:  # type: ignore[override]  # plugin API signature divergence
        self._host = host_api
        self._config = load(getattr(host_api, "settings", None))
        try:
            self._layer_queue = LayerTaskQueue(
                host_api,
                max_size=self._config.layer_queue_size,
                snapshot_fmt=self._config.layer_snapshot_fmt,
            )
            self._layer_queue.start()
        except Exception:
            logger.debug("Layer task queue initialization failed", exc_info=True)
            self._layer_queue = None
        self._ensure_adapters()

    def deactivate(self) -> None:  # type: ignore[override]  # plugin API signature divergence
        if self._layer_queue:
            self._schedule(self._layer_queue.shutdown())
            self._layer_queue = None
        self._schedule(self._session_mgr.close())
        self._pending.clear()
        self._adapters.clear()
        self._host = None

    def layer_queue(self) -> LayerTaskQueue | None:
        return self._layer_queue

    async def cleanup(self) -> None:
        """Finish owned asynchronous resources before registry shutdown returns."""
        if self._layer_queue is not None:
            await self._layer_queue.shutdown()
            self._layer_queue = None
        await self._session_mgr.close()
        self._pending.clear()
        self._adapters.clear()
        self._host = None

    def _schedule(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)  # noqa: RUF006
        except RuntimeError:
            asyncio.run(coro)

    def _ensure_config(self) -> LLMConfig:
        settings = getattr(self._host, "settings", None) if self._host else None
        config = load(settings)
        self._config = config
        return config

    def _ensure_adapters(self) -> None:
        for key, adapter_cls in _SUPPORTED_PROVIDERS.items():
            if key not in self._adapters:
                self._adapters[key] = adapter_cls()

    def _require_adapter(self, provider: str) -> ProviderAdapter:
        self._ensure_adapters()
        if provider not in self._adapters:
            raise InvalidRequestError(f"unsupported provider: {provider}")
        return self._adapters[provider]

    def _build_provider_config(
        self,
        config: LLMConfig,
        provider: str,
        override: str | None,
        api_key: str,
    ) -> ProviderConfig:
        # ``api_key`` is sourced from ``use_api_key()`` and is only
        # valid for the duration of the surrounding ``with`` block.
        # The returned ``ProviderConfig`` MUST be consumed inside the
        # same with-block — once it exits, the SecretProvider has
        # released its reference and the value is treated as expired.
        model = override or config.models.get(provider) or provider
        return ProviderConfig(
            api_key=api_key,
            model=model,
            timeout=config.timeout,
            verify_ssl=config.verify_ssl,
        )

    def _normalize_attachments(self, raw: Iterable[Any]) -> list[ImageAttachment]:
        attachments: list[ImageAttachment] = []
        for item in raw or []:
            if isinstance(item, ImageAttachment):
                attachments.append(item)
                continue
            if isinstance(item, Mapping):
                data = item.get("data")
                if isinstance(data, str):
                    data = base64.b64decode(data)
                if isinstance(data, bytearray):
                    data = bytes(data)
                if not isinstance(data, (bytes, bytearray)):
                    raise InvalidRequestError("attachment data must be bytes")
                layer_meta = item.get("layer")
                metadata = None
                if isinstance(layer_meta, Mapping):
                    metadata = LayerMetadata(
                        id=str(layer_meta.get("id", "")),
                        name=str(layer_meta.get("name", "")),
                        visible=bool(layer_meta.get("visible", True)),
                        opacity=float(layer_meta.get("opacity", 1.0)),
                        blend_mode=str(layer_meta.get("blend_mode", "normal")),
                    )
                attachment = ImageAttachment(
                    id=str(item.get("id", uuid4().hex)),
                    source=str(item.get("source", "file")),
                    mime=str(item.get("mime", "image/png")),
                    data=data,
                    width=int(item.get("width", 0)),
                    height=int(item.get("height", 0)),
                    layer=metadata,
                )
                attachments.append(attachment)
        return attachments

    def _coerce_messages(self, value: Any) -> Iterable[Mapping[str, Any]]:
        if not value:
            return []
        if isinstance(value, str):
            return [{"role": "user", "content": value}]
        if isinstance(value, Mapping):
            return [value]
        if isinstance(value, Iterable):
            return value  # type: ignore[return-value]  # Iterable narrowed at runtime
        raise InvalidRequestError("prompt must be mapping, iterable, or string")

    def _absorb_chunk(self, pending: PendingJob, chunk: ProviderChunk) -> None:
        if chunk.delta:
            pending.buffer.append(chunk.delta)
        if chunk.usage:
            pending.usage = chunk.usage


def create_plugin() -> LLMGatewayPlugin:
    """Factory called by host to instantiate the plugin."""

    return LLMGatewayPlugin()
