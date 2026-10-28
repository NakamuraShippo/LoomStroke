"""Snap drawing input without changing transform tools' edge-based snapping."""

from __future__ import annotations

from typing import Any

from ...plugins.sdk.snap import snap_position

_DRAWING_TOOLS = frozenset(
    {"basic_pen", "basic_eraser", "effect_pen", "vector_path_tool", "vector_pen_precise"}
)


def snap_drawing_position(widget: Any, x: float, y: float) -> tuple[float, float]:
    getter = getattr(widget, "_active_tool_id", None)
    if not callable(getter) or getter() not in _DRAWING_TOOLS:
        return x, y
    host = getattr(widget, "_host_api", None)
    settings = getattr(host, "settings", None)
    if settings is None:
        return x, y
    controller = getattr(widget, "_guide_controller", None)
    guides = controller is not None and controller.guides_visible and bool(controller.guides())
    if not guides and not settings.get("view/composition_overlay_kinds", []):
        return x, y
    return snap_position(host, x, y, grid_snap=False)
