"""Live-app runtime controller for the LoomStroke MCP HTTP transport."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomstroke.platform.settings_schema import (
    DEFAULT_MCP_LIVE_HTTP_PORT,
    MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY,
    MCP_LIVE_HTTP_ENABLED_KEY,
    MCP_LIVE_HTTP_PORT_KEY,
    MCP_LIVE_HTTP_ROOTS_KEY,
    MCP_LIVE_HTTP_SESSION_TOKEN_KEY,
    MCP_LIVE_HTTP_START_ON_LAUNCH_KEY,
)

from .protocol import McpProtocolRouter
from .safety import make_session_token, normalize_roots
from .service import LoomStrokeMcpService
from .transports.streamable_http import StreamableHttpState, StreamableHttpTransport
from .types import McpStructuredError, ReasonCode

logger = logging.getLogger(__name__)

DEFAULT_MCP_LIVE_HTTP_HOST = "127.0.0.1"
DEFAULT_MCP_LIVE_HTTP_PATH = "/mcp"

# Upper bound for awaiting the owned SAM3 provider's close() during stop().
# A busy local worker holds its request lock for up to the segment timeout
# (60s); shutdown must not wait for that, so time out and fall back to a
# best-effort synchronous terminate.
SAM3_PROVIDER_CLOSE_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class LiveMcpEndpointInfo:
    """Current live HTTP endpoint state.

    The session token is persisted in local settings and must not be logged.
    """

    running: bool
    host: str
    port: int
    path: str
    session_token: str
    allowed_origins: tuple[str, ...]
    active_connections: int = 0

    @property
    def url(self) -> str | None:
        if not self.running or self.port <= 0:
            return None
        return f"http://{self.host}:{self.port}{self.path}"

    def to_dict(self, *, include_session_token: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "url": self.url,
            "allowedOrigins": list(self.allowed_origins),
            "activeConnections": self.active_connections,
        }
        if include_session_token:
            payload["sessionToken"] = self.session_token
        return payload


StatusCallback = Callable[[LiveMcpEndpointInfo], None]


class LiveMcpRuntimeController:
    """Owns the opt-in live MCP HTTP listener for an application session.

    Construction is inert. A network listener is opened only by ``start()`` or
    ``start_if_configured()``.
    """

    def __init__(
        self,
        *,
        service: LoomStrokeMcpService | None = None,
        host_api: Any = None,
        tool_manager_provider: Callable[[], Any] | None = None,
        document_operation_handler: Any = None,
        document_state_provider: Callable[[], Mapping[str, Any]] | None = None,
        editing_services_provider: Callable[[], Mapping[str, Any]] | None = None,
        canvas_service: Any = None,
        page_service: Any = None,
        settings: Any = None,
        roots: Sequence[str | Path] | None = None,
        allowed_origins: Sequence[str] | None = None,
        host: str = DEFAULT_MCP_LIVE_HTTP_HOST,
        port: int | None = None,
        path: str = DEFAULT_MCP_LIVE_HTTP_PATH,
        confirmation_callback: Any = None,
        snapshot_provider: Any = None,
        invalidation_provider: Any = None,
        generation_submitter: Any = None,
        generation_canceller: Any = None,
        ai_effect_preset_lister: Any = None,
        ai_effect_preset_applier: Any = None,
        sam3_provider: Any = None,
        sam3_provider_resolver: Any = None,
        status_callback: StatusCallback | None = None,
    ) -> None:
        self.settings = settings
        self._explicit_roots = tuple(roots) if roots is not None else None
        self._explicit_allowed_origins = (
            tuple(allowed_origins) if allowed_origins is not None else None
        )
        self.host = host
        self._explicit_port = port
        self.path = path if path.startswith("/") else f"/{path}"
        self.status_callback = status_callback
        self.service = service or LoomStrokeMcpService(
            mode="live_http",
            host_api=host_api,
            tool_manager_provider=tool_manager_provider,
            document_operation_handler=document_operation_handler,
            document_state_provider=document_state_provider,
            editing_services_provider=editing_services_provider,
            canvas_service=canvas_service,
            page_service=page_service,
            settings=settings,
            roots=self._resolve_roots(),
            confirmation_callback=confirmation_callback,
            snapshot_provider=snapshot_provider,
            invalidation_provider=invalidation_provider,
            generation_submitter=generation_submitter,
            generation_canceller=generation_canceller,
            ai_effect_preset_lister=ai_effect_preset_lister,
            ai_effect_preset_applier=ai_effect_preset_applier,
        )
        self.router = McpProtocolRouter(self.service)
        if editing_services_provider is not None:
            from .production_generation_adapter import (  # noqa: PLC0415
                install_production_generation_adapter,
            )

            install_production_generation_adapter(self.service)
        self._transport: StreamableHttpTransport | None = None
        # Explicitly injected providers are caller-owned: attached once here,
        # never re-resolved on start() and never detached/closed on stop().
        self._explicit_sam3_provider = sam3_provider
        self._sam3_provider_resolver = sam3_provider_resolver
        self._owned_sam3_provider: Any = None
        self._lifecycle_lock = asyncio.Lock()
        if sam3_provider is not None:
            self.service.attach_sam3_provider(sam3_provider)
        self._ensure_session_token()

    @property
    def transport(self) -> StreamableHttpTransport | None:
        return self._transport

    @property
    def is_running(self) -> bool:
        return self._transport is not None

    @property
    def live_http_enabled(self) -> bool:
        return _as_bool(_settings_value(self.settings, MCP_LIVE_HTTP_ENABLED_KEY, False))

    @property
    def start_on_launch_enabled(self) -> bool:
        return _as_bool(_settings_value(self.settings, MCP_LIVE_HTTP_START_ON_LAUNCH_KEY, False))

    async def start_if_configured(self) -> LiveMcpEndpointInfo | None:
        """Start only when both live HTTP and start-on-launch are enabled."""

        if not self.live_http_enabled or not self.start_on_launch_enabled:
            return None
        return await self.start()

    async def start(self) -> LiveMcpEndpointInfo:
        async with self._lifecycle_lock:
            return await self._start_locked()

    async def _start_locked(self) -> LiveMcpEndpointInfo:
        if self._transport is not None:
            return self.endpoint_info()

        # Policy refresh first: it can raise (e.g. invalid roots) and runs
        # before there is any owned provider to roll back.
        self._refresh_service_policy()
        await self._resolve_and_attach_sam3_provider()
        transport: StreamableHttpTransport | None = None
        try:
            transport = StreamableHttpTransport(
                self.router,
                safety=self.service.safety,
                host=self.host,
                port=self._resolve_port(),
                path=self.path,
                allowed_origins=self._resolve_allowed_origins(),
                active_connection_callback=self._on_transport_state,
            )
            self._transport = transport
            state = await transport.start()
        except Exception:
            if transport is not None:
                await transport.stop()
            self._transport = None
            await self._release_owned_sam3_provider()
            raise

        return self._endpoint_from_state(state, running=True)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        from .llm_tools import close_jobs  # noqa: PLC0415

        await close_jobs(self.service)
        transport = self._transport
        if transport is None:
            # A failed start() already rolled back, but release defensively so
            # shutdown never leaves an owned provider (and its worker) behind.
            await self._release_owned_sam3_provider()
            return
        await transport.stop()
        self._transport = None
        await self._release_owned_sam3_provider()
        self._emit_status(self.endpoint_info())

    async def _resolve_and_attach_sam3_provider(self) -> None:
        if self._explicit_sam3_provider is not None:
            return
        if self._owned_sam3_provider is None and self.service.sam3_provider is not None:
            # The caller wired a provider into the service directly; treat it
            # as externally managed and never overwrite or close it.
            return
        await self._release_owned_sam3_provider()
        resolver = self._sam3_provider_resolver
        if resolver is None:
            from loomstroke.ai_selection import (  # noqa: PLC0415
                resolve_configured_sam3_provider_binding,
            )

            resolver = resolve_configured_sam3_provider_binding
        try:
            binding = await resolver(self.settings)
        except Exception as exc:  # noqa: BLE001 - SAM3 issues must not block MCP start.
            logger.warning(
                "LoomStroke MCP SAM3 provider resolve failed: %s",
                type(exc).__name__,
            )
            return
        provider = getattr(binding, "provider", None)
        if provider is None:
            return
        self._owned_sam3_provider = provider
        self.service.attach_sam3_provider(provider)
        logger.info(
            "LoomStroke MCP SAM3 provider attached (runtime=%s)",
            getattr(binding, "runtime", "unknown"),
        )

    async def _release_owned_sam3_provider(self) -> None:
        provider = self._owned_sam3_provider
        if provider is None:
            return
        self._owned_sam3_provider = None
        if self.service.sam3_provider is provider:
            self.service.attach_sam3_provider(None)
        try:
            await asyncio.wait_for(provider.close(), timeout=SAM3_PROVIDER_CLOSE_TIMEOUT_S)
        except asyncio.CancelledError:
            # A cancelled shutdown still must not orphan the worker process.
            _terminate_sam3_worker_sync(provider)
            raise
        except Exception as exc:  # noqa: BLE001 - shutdown must not hang on SAM3 teardown.
            logger.warning(
                "LoomStroke MCP SAM3 provider close failed: %s",
                type(exc).__name__,
            )
            _terminate_sam3_worker_sync(provider)

    async def restart(self) -> LiveMcpEndpointInfo:
        await self.stop()
        return await self.start()

    def rotate_session_token(self) -> LiveMcpEndpointInfo:
        """Rotate and persist the bearer token."""

        self.service.safety.session_token = make_session_token()
        _store_settings_value(
            self.settings,
            MCP_LIVE_HTTP_SESSION_TOKEN_KEY,
            self.service.safety.session_token,
        )
        info = self.endpoint_info()
        self._emit_status(info)
        return info

    def sync_session_token_from_settings(self) -> LiveMcpEndpointInfo:
        """Refresh the in-memory bearer token from settings without restarting."""

        self._ensure_session_token()
        info = self.endpoint_info()
        self._emit_status(info)
        return info

    def endpoint_info(self) -> LiveMcpEndpointInfo:
        if self._transport is not None:
            return self._endpoint_from_state(self._transport.state, running=True)
        return LiveMcpEndpointInfo(
            running=False,
            host=self.host,
            port=self._resolve_port(),
            path=self.path,
            session_token=self.service.safety.session_token,
            allowed_origins=tuple(self.service.safety.allowed_origins),
            active_connections=0,
        )

    def _refresh_service_policy(self) -> None:
        self.service.safety.settings = self.settings
        self.service.safety.roots = normalize_roots(self._resolve_roots())
        self.service.safety.session_token = self._ensure_session_token()

    def _ensure_session_token(self) -> str:
        token = _as_session_token(
            _settings_value(self.settings, MCP_LIVE_HTTP_SESSION_TOKEN_KEY, "")
        )
        if not token:
            token = self.service.safety.session_token or make_session_token()
            _store_settings_value(self.settings, MCP_LIVE_HTTP_SESSION_TOKEN_KEY, token)
        self.service.safety.session_token = token
        return token

    def _resolve_port(self) -> int:
        if self._explicit_port is not None:
            port = int(self._explicit_port)
        else:
            port = _as_int(
                _settings_value(
                    self.settings,
                    MCP_LIVE_HTTP_PORT_KEY,
                    DEFAULT_MCP_LIVE_HTTP_PORT,
                ),
                DEFAULT_MCP_LIVE_HTTP_PORT,
            )
        if port < 0 or port > 65535:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Invalid LoomStroke MCP HTTP port.",
                details={"port": port},
            )
        return port

    def _resolve_roots(self) -> tuple[str | Path, ...]:
        if self._explicit_roots is not None:
            return self._explicit_roots
        return _as_path_sequence(_settings_value(self.settings, MCP_LIVE_HTTP_ROOTS_KEY, ()))

    def _resolve_allowed_origins(self) -> tuple[str, ...] | None:
        if self._explicit_allowed_origins is not None:
            return tuple(origin for origin in self._explicit_allowed_origins if origin)
        origins = _as_str_sequence(
            _settings_value(self.settings, MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY, ())
        )
        return origins or None

    def _endpoint_from_state(
        self,
        state: StreamableHttpState,
        *,
        running: bool,
    ) -> LiveMcpEndpointInfo:
        return LiveMcpEndpointInfo(
            running=running,
            host=state.host,
            port=state.port,
            path=state.path,
            session_token=state.session_token,
            allowed_origins=tuple(self.service.safety.allowed_origins),
            active_connections=state.active_connections,
        )

    def _on_transport_state(self, state: StreamableHttpState) -> None:
        self._emit_status(self._endpoint_from_state(state, running=self._transport is not None))

    def _emit_status(self, info: LiveMcpEndpointInfo) -> None:
        if self.status_callback is not None:
            self.status_callback(info)


def _terminate_sam3_worker_sync(provider: Any) -> None:
    """Best-effort synchronous worker teardown when close() hangs or is cut."""

    terminate = getattr(
        getattr(provider, "_worker_client", None),  # noqa: SLF001
        "terminate_sync",
        None,
    )
    if callable(terminate):
        terminate()


def _settings_value(settings: Any, key: str, default: Any) -> Any:
    if settings is None:
        return default
    try:
        if isinstance(settings, Mapping):
            return settings.get(key, default)
        get = getattr(settings, "get", None)
        if callable(get):
            return get(key, default)
        value = getattr(settings, "value", None)
        if callable(value):
            return value(key, default)
    except Exception:
        return default
    return default


def _store_settings_value(settings: Any, key: str, value: str) -> None:
    if settings is None:
        return
    try:
        if isinstance(settings, dict):
            settings[key] = value
            return
        set_value = getattr(settings, "set", None)
        if callable(set_value):
            try:
                set_value(key, value, emit_event=False)
            except TypeError:
                set_value(key, value)
            return
        set_qvalue = getattr(settings, "setValue", None)
        if callable(set_qvalue):
            set_qvalue(key, value)
            sync = getattr(settings, "sync", None)
            if callable(sync):
                sync()
            return
        nested = getattr(settings, "settings", None)
        if nested is not None and nested is not settings:
            _store_settings_value(nested, key, value)
    except Exception:
        return


def _as_session_token(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return False


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _as_str_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        parsed = _json_list_from_string(stripped)
        if parsed is not None:
            return tuple(str(item) for item in parsed if str(item).strip())
        return (stripped,) if stripped else ()
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _as_path_sequence(value: Any) -> tuple[str | Path, ...]:
    if isinstance(value, str):
        stripped = value.strip()
        parsed = _json_list_from_string(stripped)
        if parsed is not None:
            return tuple(str(item) for item in parsed if str(item).strip())
        return (stripped,) if stripped else ()
    if isinstance(value, Sequence):
        return tuple(item for item in value if str(item).strip())
    return ()


def _json_list_from_string(value: str) -> tuple[Any, ...] | None:
    if not value:
        return ()
    if not value.startswith("["):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return tuple(parsed)


__all__ = [
    "DEFAULT_MCP_LIVE_HTTP_HOST",
    "DEFAULT_MCP_LIVE_HTTP_PATH",
    "MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY",
    "MCP_LIVE_HTTP_ENABLED_KEY",
    "MCP_LIVE_HTTP_PORT_KEY",
    "MCP_LIVE_HTTP_ROOTS_KEY",
    "MCP_LIVE_HTTP_SESSION_TOKEN_KEY",
    "MCP_LIVE_HTTP_START_ON_LAUNCH_KEY",
    "LiveMcpEndpointInfo",
    "LiveMcpRuntimeController",
]
