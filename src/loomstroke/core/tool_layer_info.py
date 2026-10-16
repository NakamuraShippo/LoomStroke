"""Nested layer information for tools, preserving the root-only query API."""

from typing import Any


def tool_layer_info(canvas: Any, layer_id: str) -> dict[str, Any] | None:
    getter = getattr(canvas, "get_layer_info", None)
    info = getter(layer_id) if callable(getter) else None
    if info is not None:
        return info
    finder = getattr(canvas, "_find_layer_by_id_tree", None)
    layer = finder(layer_id) if callable(finder) else None
    if layer is None:
        return None
    return {
        "id": layer.id,
        "name": layer.name,
        "visible": layer.visible,
        "opacity": layer.opacity,
        "blend_mode": str(layer.blend_mode.value),
        "locked": layer.locked,
        "alpha_lock": layer.alpha_lock,
        "layer_type": layer.layer_type,
    }
