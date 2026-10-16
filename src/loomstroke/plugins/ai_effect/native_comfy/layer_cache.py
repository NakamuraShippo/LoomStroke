"""Section-owned frontends, shared by layers using the same server."""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtWidgets import QWidget

from loomstroke.plugins.comfyui.server_registry import get_registry
from loomstroke.plugins.comfyui.session_target import active_url


def identity(state: Any) -> Any:
    return copy.deepcopy(
        (
            state.preset_snapshot,
            state.param_ui_state.get("comfyui_native"),
            state.target_server_id,
        )
    )


def target_url(state: Any) -> str | None:
    registry = get_registry()
    gateway = state.preset_snapshot.get("gateway_file", {})
    server_id = state.target_server_id or gateway.get("target_server_id") or "local"
    server = registry.get(server_id)
    url = active_url(registry) or (server.url if server else gateway.get("endpoint_url"))
    return url.rstrip("/") if url else None


def park(section: Any, panel: Any) -> None:
    if not isinstance(section, QWidget):
        panel.dispose()
        return
    cache = getattr(section, "_comfy_layer_cache", None)
    if cache is None:
        cache = section._comfy_layer_cache = {}
    panel.hide()
    panel._session_timer.stop()
    panel.setParent(section)
    key = panel._session_url
    previous = cache.pop(key, None)
    if previous is not None and previous is not panel:
        previous.dispose()
        previous.deleteLater()
    cache[key] = panel
    while len(cache) > 4:
        oldest = cache.pop(next(iter(cache)))
        oldest.dispose()
        oldest.deleteLater()


def take(section: Any, state: Any) -> Any:
    cache = getattr(section, "_comfy_layer_cache", {})
    panel = cache.pop(target_url(state), None)
    if panel is None:
        return None
    panel.setParent(section._ai_effect_group)
    panel._session_timer.start()
    from .layer_session import select  # noqa: PLC0415 - avoid module cycle

    select(panel, state)
    return panel


def clear(section: Any) -> None:
    cache = getattr(section, "_comfy_layer_cache", {})
    for panel in cache.values():
        panel.dispose()
        panel.deleteLater()
    cache.clear()
