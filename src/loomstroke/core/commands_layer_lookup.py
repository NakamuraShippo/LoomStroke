"""Shared raster-layer lookup for layer-level commands (simple + legacy canvases)."""

from __future__ import annotations

from typing import Any

__all__ = ["find_layer"]


def find_layer(canvas: Any, layer_id: str) -> Any | None:
    for name in ("_get_layer_by_id", "get_layer_by_id"):
        getter = getattr(canvas, name, None)
        if callable(getter):
            try:
                layer = getter(layer_id)
            except Exception:  # pragma: no cover - defensive against exotic canvases
                layer = None
            if layer is not None:
                return layer
    return None
