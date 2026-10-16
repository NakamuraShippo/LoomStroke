"""aiohttp session helpers for the LLM gateway plugin."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping

import aiohttp

from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent


class SessionManager:
    """Create and reuse a single aiohttp session for the plugin."""

    def __init__(self) -> None:
        self._settings = get_settings_manager()
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    def _ensure_external_services_allowed(self) -> None:
        if not ensure_network_services_consent(self._settings, service_name="LLM Gateway"):
            raise PermissionError(
                "LLM Gateway access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )

    async def ensure(self, *, timeout: float, verify_ssl: bool) -> aiohttp.ClientSession:
        self._ensure_external_services_allowed()
        async with self._lock:
            if self._session and not self._session.closed:
                return self._session
            timeout_cfg = aiohttp.ClientTimeout(total=timeout or None)
            # TLS verification bypass is restricted to localhost only
            if verify_ssl:
                connector = aiohttp.TCPConnector(ssl=None)
            else:
                raise PermissionError(
                    "LLM Gateway TLS verification cannot be disabled for third-party providers."
                )
            self._session = aiohttp.ClientSession(timeout=timeout_cfg, connector=connector)
            return self._session

    async def close(self) -> None:
        async with self._lock:
            session = self._session
            self._session = None
        if session and not session.closed:
            await session.close()


def merge_headers(
    base: MutableMapping[str, str] | None, extra: MutableMapping[str, str]
) -> dict[str, str]:
    """Return a merged header dictionary without mutating inputs."""

    merged: dict[str, str] = {}
    if base:
        merged.update({str(k): str(v) for k, v in base.items()})
    merged.update({str(k): str(v) for k, v in extra.items()})
    return merged
