"""Volatile, confirmed ComfyUI destination shared during one application run."""

from typing import Any


def active_url(registry: Any) -> str | None:
    value = getattr(registry, "_session_target_url", None)
    return value if isinstance(value, str) and value else None


def confirm(registry: Any, url: str, *, explicit: bool = False) -> None:
    if isinstance(url, str) and url and (explicit or active_url(registry) is None):
        registry._session_target_url = url.rstrip("/")
        if registry.get_by_url(url) is None:
            from .server_registry import ComfyUIServer  # noqa: PLC0415

            registry.add(ComfyUIServer.from_url(url))


async def bind_service(service: Any, registry: Any) -> None:
    url = active_url(registry)
    if not url:
        return
    connection = await registry.get_or_create_connection(url)
    if getattr(service, "_connection", None) is connection:
        return
    service._connection = connection
    cache = getattr(service, "_object_info_cache", None)
    if cache is not None:
        cache.nodes = {}
        cache.models = {}
