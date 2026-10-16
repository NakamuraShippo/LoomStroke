"""Read-only status of Preferences-managed ComfyUI endpoints."""

import asyncio
from typing import Any

import aiohttp

from loomstroke.plugins.common.outbound_policy import is_external_services_allowed

from .local_runtime import STARTUP_STATES, server_ready
from .server_registry import configured_servers_from_settings


async def statuses(settings: Any) -> list[tuple[str, str, str, str]]:
    servers = configured_servers_from_settings(settings)
    if not is_external_services_allowed(settings):
        return [(s.name, s.url, "External services disabled", "") for s in servers]

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:

        async def check(server: Any) -> tuple[str, str, str, str]:
            state, detail = STARTUP_STATES.get(server.url, ("Stopped or unreachable", ""))
            try:
                if await server_ready(session, server.url):
                    state, detail = "Running", ""
            except (aiohttp.ClientError, TimeoutError, ValueError):
                if state not in {"Starting", "Startup failed"}:
                    state = "Stopped or unreachable"
            return server.name, server.url, state, detail

        return list(await asyncio.gather(*(check(server) for server in servers)))
