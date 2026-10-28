"""ComfyUI Server Registry — multi-server connection management.

Phase 10c (2026-04-30) follow-up to the per-preset endpoint URL swap
pattern (``_switch_service_endpoint``) introduced in Phase 8a-bridge.
The swap pattern mutated a single shared service's ``_connection`` to
point at the preset's URL, with two consequences:

1. After a preset with explicit URL ran, every subsequent preset with
   an empty URL inherited the swap target — so users running mixed
   local/remote workflows hit "wrong server" routing on the 2nd+ run.
2. The ComfyUI panel saw the connection mutate underneath it, which
   is technically observable as a status-bar flicker.

This module replaces that with a **per-URL connection cache**. Each
distinct ComfyUI URL gets its own :class:`AsyncComfyUIConnection`,
held by the registry for the lifetime of the process. The
:class:`ComfyUIGateway` resolves the connection it needs at submit
time without ever mutating shared state — concurrent submits to
different URLs no longer interfere.

Scope (2026-04-30): the minimum to abolish the swap pattern. The
registry stores ad-hoc URL → connection mappings. Persisted server
metadata (id / name / color_hint) and the Settings UI for managing
servers come in the next slice (Phase 10c-ui).

Usage::

    registry = ComfyUIServerRegistry()
    # Migration of an existing single-server setup:
    registry.add(ComfyUIServer.from_url("http://127.0.0.1:8188", name="Local"))

    # Gateway resolves on submit:
    conn = await registry.get_or_create_connection("http://192.168.0.10:8188")
    # ``conn`` is a per-URL AsyncComfyUIConnection that survives
    # subsequent submits without being clobbered.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from loomstroke.plugins.comfyui.async_connection import AsyncComfyUIConnection

logger = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
DEFAULT_LOCAL_COMFYUI_URL = "http://127.0.0.1:8188"
EXTERNAL_SERVERS_SETTING_KEY = "comfyui.external_servers"


def _slug(value: str, fallback: str = "server") -> str:
    """Conservatively slugify a free-form string for use as a server_id."""
    normalized = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return normalized or fallback


def _normalize_url(url: str) -> str:
    """Strip trailing slashes so URL comparisons are stable."""
    return url.strip().rstrip("/")


def _normalize_configured_url(url: str) -> str:
    """Normalise user-entered settings URLs for connection use."""
    cleaned = _normalize_url(url)
    if cleaned and "://" not in cleaned:
        return f"http://{cleaned}"
    return cleaned


def _settings_get(settings: Any, key: str, default: Any = None) -> Any:
    getter = getattr(settings, "get", None)
    if not callable(getter):
        return default
    return getter(key, default)


def _external_server_id(index: int, entry: dict[str, Any]) -> str:
    raw = entry.get("id")
    if isinstance(raw, str) and raw.strip():
        return _slug(raw, fallback=f"external_{index + 1}")
    return "external" if index == 0 else f"external_{index + 1}"


def _reserve_external_server_ids(identifiers: Iterable[tuple[Any, bool]]) -> set[str]:
    """Keep every existing row's raw and registry-normalized identity reserved."""
    reserved = {"local"}
    configured_index = 0
    for value, has_url in identifiers:
        identifier = str(value).strip() if value else ""
        if identifier:
            reserved.update((identifier, _slug(identifier)))
            if has_url:
                reserved.add(_external_server_id(configured_index, {"id": identifier}))
                # The existing parser uses this fallback when that ID is already taken.
                reserved.add(f"external_{configured_index + 1}")
        if has_url:
            configured_index += 1
    return reserved


def _allocate_external_server_id(index: int, reserved: set[str]) -> str:
    """Allocate only a new row ID, preserving the usual external/N ordering."""
    candidate = _external_server_id(index, {})
    while candidate in reserved:
        index += 1
        candidate = _external_server_id(index, {})
    reserved.add(candidate)
    return candidate


def configured_servers_from_settings(settings: Any) -> list[ComfyUIServer]:
    """Return the Preferences-managed ComfyUI server list.

    Preferences is the single write location for endpoint URLs. A blank
    Local URL intentionally falls back to ComfyUI's default address.
    ``comfyui.external_url`` is kept as a legacy first-external slot.
    """
    raw_local_url = str(_settings_get(settings, "comfyui.local_url", "") or "")
    local_url = _normalize_configured_url(raw_local_url)
    if not local_url:
        local_url = DEFAULT_LOCAL_COMFYUI_URL
    servers = [
        ComfyUIServer(
            id="local",
            name="Local",
            url=local_url,
            auto_detect=True,
            is_default=True,
        )
    ]

    raw_entries = _settings_get(settings, EXTERNAL_SERVERS_SETTING_KEY, [])
    entries: list[dict[str, Any]] = []
    if isinstance(raw_entries, list):
        entries = [entry for entry in raw_entries if isinstance(entry, dict)]
    else:
        legacy_url = str(_settings_get(settings, "comfyui.external_url", "") or "").strip()
        if legacy_url:
            entries = [{"id": "external", "name": "External", "url": legacy_url}]

    if not entries:
        legacy_url = str(_settings_get(settings, "comfyui.external_url", "") or "").strip()
        if legacy_url:
            entries = [{"id": "external", "name": "External", "url": legacy_url}]

    used_ids = {"local"}
    for index, entry in enumerate(entries):
        url = _normalize_configured_url(str(entry.get("url", "") or ""))
        if not url:
            continue
        server_id = _external_server_id(index, entry)
        if server_id in used_ids:
            server_id = f"external_{index + 1}"
        used_ids.add(server_id)
        fallback_name = "External" if index == 0 else f"External {index + 1}"
        name = str(entry.get("name", "") or "").strip() or fallback_name
        servers.append(
            ComfyUIServer(
                id=server_id,
                name=name,
                url=url,
                auto_detect=False,
                is_default=False,
            )
        )
    return servers


def sync_registry_from_settings(
    settings: Any,
    registry: ComfyUIServerRegistry | None = None,
) -> ComfyUIServerRegistry:
    """Replace registered server metadata with the Preferences list."""
    target = registry or get_registry()
    servers = configured_servers_from_settings(settings)
    target.load_from_config(
        {
            "servers": [
                {
                    "id": server.id,
                    "name": server.name,
                    "url": server.url,
                    "auto_detect": server.auto_detect,
                    "is_default": server.is_default,
                    "color_hint": server.color_hint,
                }
                for server in servers
            ],
            "default_server_id": "local",
        }
    )
    return target


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ComfyUIServer:
    """A single ComfyUI endpoint the user can target.

    The ``id`` is a stable identifier used by ``AIEffectState.target_server_id``
    so a preset can pin to one server across user sessions. ``url`` is the
    HTTP base URL (no trailing slash). The remaining fields are presentation
    hints for the (future) Settings UI.
    """

    id: str
    name: str
    url: str
    auto_detect: bool = False
    is_default: bool = False
    color_hint: str = "#888888"

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        name: str | None = None,
        auto_detect: bool = False,
        is_default: bool = False,
    ) -> ComfyUIServer:
        """Build a server entry from a raw URL with a generated ``id``.

        The id derives from the URL's host:port to give returning sessions
        a chance of finding the same entry without remembering a UUID. If
        the URL is malformed (e.g. ``""``) a new UUID is used instead.
        """
        normalized = _normalize_url(url)
        # Reasonable default name: the host:port slug, or the full URL
        # when the slug would be empty (unlikely in practice).
        slug_source = normalized.split("//", 1)[-1] or normalized
        slug = _slug(slug_source, fallback=f"server-{uuid.uuid4().hex[:8]}")
        return cls(
            id=slug,
            name=name or slug_source or "ComfyUI",
            url=normalized,
            auto_detect=auto_detect,
            is_default=is_default,
        )


@dataclass(slots=True)
class ServerStatus:
    """Per-server connection state. Used by the registry to surface
    "is this server reachable?" without forcing every caller to manage
    the underlying connection lifecycle.

    Phase 10c uses this only for internal bookkeeping; the Settings UI
    will read it once the per-server status indicators land.
    """

    server_id: str
    state: str = "disconnected"  # "disconnected" / "connecting" / "connected" / "error"
    last_ping_ts: float = 0.0
    last_error: str | None = None
    object_info_cached: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ComfyUIServerRegistry:
    """Per-process owner of every active ComfyUI connection.

    The registry guarantees the swap-pattern bug cannot recur because
    each server has its own dedicated :class:`AsyncComfyUIConnection`
    instance — we never mutate a shared connection's ``base_url``.

    Thread safety: Qt main thread only. The async methods cooperate via
    qasync; no internal locks are taken because each ``connect`` /
    ``get_or_create_connection`` call is short-lived and the registry
    is not exposed to background threads.

    Phase 10c-minimal: the registry is plain Python (no QObject /
    Signals). When the Settings UI lands and needs change notifications
    we'll bolt on a simple callable-list observer rather than QObject
    inheritance — the registry is a single-process data structure, not
    a Qt widget.
    """

    def __init__(self) -> None:
        self._servers: dict[str, ComfyUIServer] = {}
        self._statuses: dict[str, ServerStatus] = {}
        # URL → AsyncComfyUIConnection, indexed by the raw URL so an
        # ad-hoc lookup (preset specifies a URL that has not been
        # registered as a server entry) auto-creates and reuses across
        # subsequent submits. The reverse map ``_url_to_server_id`` is
        # populated when the URL belongs to a known server.
        self._connections: dict[str, AsyncComfyUIConnection] = {}
        self._url_to_server_id: dict[str, str] = {}
        self._default_server_id: str | None = None

    # ----- registration ----------------------------------------------------

    def add(self, server: ComfyUIServer) -> ComfyUIServer:
        """Register a server. Idempotent on (id, url) — a duplicate
        ``id`` overwrites the existing entry, a duplicate URL alone
        is allowed (two display-name aliases for the same endpoint).
        """
        normalized = ComfyUIServer(
            id=server.id,
            name=server.name,
            url=_normalize_url(server.url),
            auto_detect=server.auto_detect,
            is_default=server.is_default,
            color_hint=server.color_hint,
        )
        previous = self._servers.get(normalized.id)
        if previous is not None and previous.url != normalized.url:
            self._url_to_server_id.pop(previous.url, None)
        self._servers[normalized.id] = normalized
        self._statuses.setdefault(normalized.id, ServerStatus(server_id=normalized.id))
        self._url_to_server_id[normalized.url] = normalized.id
        if normalized.is_default or self._default_server_id is None:
            self._default_server_id = normalized.id
        return normalized

    def remove(self, server_id: str) -> bool:
        """Remove a server. Drops the cached connection (the connection
        is NOT disconnected here — callers concerned about sockets
        should call :meth:`shutdown_connection` first). Returns ``True``
        when an entry was actually removed."""
        server = self._servers.pop(server_id, None)
        if server is None:
            return False
        self._statuses.pop(server_id, None)
        self._url_to_server_id.pop(server.url, None)
        if self._default_server_id == server_id:
            # Promote the next available server (insertion order) so the
            # registry never points at a non-existent default.
            self._default_server_id = next(iter(self._servers), None)
        return True

    def get(self, server_id: str) -> ComfyUIServer | None:
        return self._servers.get(server_id)

    def get_by_url(self, url: str) -> ComfyUIServer | None:
        sid = self._url_to_server_id.get(_normalize_url(url))
        if sid is None:
            return None
        return self._servers.get(sid)

    def list_all(self) -> list[ComfyUIServer]:
        return list(self._servers.values())

    @property
    def default_server_id(self) -> str | None:
        return self._default_server_id

    def set_default(self, server_id: str) -> bool:
        if server_id not in self._servers:
            return False
        # Clear is_default on every other server so the dataclass is the
        # single source of truth (matches the round-tripped JSON shape).
        for sid, server in self._servers.items():
            server.is_default = sid == server_id
        self._default_server_id = server_id
        return True

    # ----- connection lifecycle -------------------------------------------

    def get_connection(self, server_id_or_url: str) -> AsyncComfyUIConnection | None:
        """Return an existing connection for ``server_id_or_url``, or
        ``None`` if one has not been created yet."""
        url = self._resolve_url(server_id_or_url)
        if url is None:
            return None
        return self._connections.get(url)

    def get_object_info_cache(self, server_id_or_url: str) -> dict[str, Any] | None:
        """Return cached ``object_info`` for one registered server."""
        status = self._status_for(server_id_or_url, create_for_url=False)
        if status is None:
            return None
        cached = status.object_info_cached
        return cached if isinstance(cached, dict) and cached else None

    def set_object_info_cache(
        self, server_id_or_url: str, object_info: dict[str, Any] | None
    ) -> None:
        """Store cached ``object_info`` for one server id / URL."""
        status = self._status_for(server_id_or_url, create_for_url=True)
        if status is None:
            return
        if isinstance(object_info, dict) and object_info:
            status.object_info_cached = object_info
            status.state = "connected"
            status.last_error = None
        else:
            status.object_info_cached = None

    async def get_or_create_connection(self, server_id_or_url: str) -> AsyncComfyUIConnection:
        """Return the connection for the given server, creating it on
        first use.

        ``server_id_or_url`` accepts either a registered server's id or
        a raw URL. When given a URL that is not known to the registry
        an ad-hoc :class:`ComfyUIServer` entry is auto-created so the
        URL has stable presentation metadata for the Settings UI when
        it lands.

        The connection itself is NOT auto-connected — callers must
        invoke ``await connection.connect()`` explicitly. This keeps
        the registry purely as a connection allocator and lets the
        gateway / panel choose when to incur the network round-trip.
        """
        from .session_target import active_url  # noqa: PLC0415

        url = active_url(self) or self._resolve_url(server_id_or_url)
        if url is None:
            raise ValueError(
                f"server_id_or_url={server_id_or_url!r} is neither a "
                "registered server id nor a recognisable URL"
            )
        # If the URL was supplied directly but no server entry exists for
        # it yet, auto-register one so the URL has stable presentation
        # metadata for the Settings UI when it lands.
        if url not in self._url_to_server_id:
            self.add(ComfyUIServer.from_url(url))
        cached = self._connections.get(url)
        if cached is not None:
            return cached
        connection = self._build_connection(url)
        self._connections[url] = connection
        return connection

    async def shutdown_connection(self, server_id_or_url: str) -> bool:
        """Disconnect and forget a cached connection. Best-effort —
        failures during disconnect are logged at debug level."""
        url = self._resolve_url(server_id_or_url)
        if url is None:
            return False
        conn = self._connections.pop(url, None)
        if conn is None:
            return False
        try:
            disconnect_fn = getattr(conn, "disconnect", None)
            if callable(disconnect_fn):
                result = disconnect_fn()
                if asyncio.iscoroutine(result):
                    await result
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.debug(
                "ComfyUIServerRegistry.shutdown_connection: disconnect failed " "for %s — %s",
                url,
                exc,
                exc_info=True,
            )
        return True

    async def shutdown_all(self) -> None:
        """Disconnect every cached connection. Called at app shutdown."""
        for url in list(self._connections):
            await self.shutdown_connection(url)

    # ----- (de)serialisation ----------------------------------------------

    def to_config(self) -> dict[str, Any]:
        """Serialise the registry to the ``settings.json`` shape.

        Mirrors §4.2 of the spec — ``servers`` list + top-level
        ``default_server_id`` + ``warn_on_public_url`` flag (defaulted
        to True until the Settings UI exposes the toggle).
        """
        return {
            "servers": [
                {
                    "id": s.id,
                    "name": s.name,
                    "url": s.url,
                    "auto_detect": s.auto_detect,
                    "is_default": s.is_default,
                    "color_hint": s.color_hint,
                }
                for s in self._servers.values()
            ],
            "default_server_id": self._default_server_id,
            "warn_on_public_url": True,
        }

    def load_from_config(self, cfg: dict[str, Any]) -> None:
        """Hydrate the registry from a settings dict produced by
        :meth:`to_config`. Existing entries are cleared."""
        self._servers.clear()
        self._statuses.clear()
        self._url_to_server_id.clear()
        self._default_server_id = None

        servers = cfg.get("servers")
        if isinstance(servers, list):
            for entry in servers:
                if not isinstance(entry, dict):
                    continue
                try:
                    self.add(
                        ComfyUIServer(
                            id=str(entry["id"]),
                            name=str(entry.get("name", "ComfyUI")),
                            url=str(entry.get("url", "")),
                            auto_detect=bool(entry.get("auto_detect", False)),
                            is_default=bool(entry.get("is_default", False)),
                            color_hint=str(entry.get("color_hint", "#888888")),
                        )
                    )
                except (KeyError, ValueError) as exc:
                    logger.warning("Skipping malformed server entry %r: %s", entry, exc)

        default_id = cfg.get("default_server_id")
        if isinstance(default_id, str) and default_id in self._servers:
            self._default_server_id = default_id

    def migrate_legacy_single_server(
        self, legacy_url: str | None, *, name: str = "Local"
    ) -> ComfyUIServer | None:
        """One-shot migration of a pre-Phase-10c single-server setting.

        When a fresh registry is loaded and the legacy ``comfyui.url``
        setting still holds a URL, call this to upgrade it to a server
        entry. No-op when ``legacy_url`` is empty or the URL is already
        registered.
        """
        if not legacy_url:
            return None
        url = _normalize_url(legacy_url)
        existing = self.get_by_url(url)
        if existing is not None:
            return existing
        server = ComfyUIServer.from_url(url, name=name, auto_detect=True, is_default=True)
        return self.add(server)

    # ----- internals -------------------------------------------------------

    def _resolve_url(self, server_id_or_url: str) -> str | None:
        """Return the canonical URL for an id-or-URL input, or ``None``
        when the input is neither a known id nor a recognisable URL.

        Url-shape detection is intentionally permissive: any string
        containing ``://`` is treated as a URL. Anything else is
        treated as a server_id and looked up; misses return ``None``
        so the caller can decide whether to auto-register.
        """
        if server_id_or_url in self._servers:
            return self._servers[server_id_or_url].url
        if "://" in server_id_or_url:
            normalized = _normalize_url(server_id_or_url)
            return normalized
        return None

    def _build_connection(self, url: str) -> AsyncComfyUIConnection:
        """Build and return a fresh :class:`AsyncComfyUIConnection` for
        the URL. Lazy-imports the connection class so the registry
        module itself does not pull Qt at import time.
        """
        from loomstroke.platform import get_async_manager  # noqa: PLC0415

        from .async_connection import AsyncComfyUIConnection  # noqa: PLC0415

        async_manager = get_async_manager()
        return AsyncComfyUIConnection(async_manager=async_manager, base_url=url)

    def _status_for(
        self,
        server_id_or_url: str,
        *,
        create_for_url: bool,
    ) -> ServerStatus | None:
        url = self._resolve_url(server_id_or_url)
        if url is None:
            return None
        server_id = self._url_to_server_id.get(url)
        if server_id is None and create_for_url and "://" in server_id_or_url:
            server_id = self.add(ComfyUIServer.from_url(url)).id
        if server_id is None:
            return None
        return self._statuses.setdefault(server_id, ServerStatus(server_id=server_id))


# Module-level singleton. Filled by ``app.py`` at startup so any plugin
# that needs to reach the registry can ``from . import server_registry``
# rather than threading a reference through every constructor.
_REGISTRY: ComfyUIServerRegistry | None = None


def get_registry() -> ComfyUIServerRegistry:
    """Return the process-wide registry, creating it if needed."""
    global _REGISTRY  # noqa: PLW0603 - module-level lazy singleton
    if _REGISTRY is None:
        _REGISTRY = ComfyUIServerRegistry()
    return _REGISTRY


def reset_registry_for_tests() -> None:
    """Clear the module-level singleton. Tests call this in fixture
    teardown so leakage between cases stays impossible."""
    global _REGISTRY  # noqa: PLW0603 - test utility
    _REGISTRY = None


__all__ = [
    "DEFAULT_LOCAL_COMFYUI_URL",
    "EXTERNAL_SERVERS_SETTING_KEY",
    "ComfyUIServer",
    "ComfyUIServerRegistry",
    "ServerStatus",
    "configured_servers_from_settings",
    "get_registry",
    "reset_registry_for_tests",
    "sync_registry_from_settings",
]
