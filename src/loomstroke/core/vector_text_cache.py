"""Canvas-owned cache access for vector text rendering."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtGui import QPainterPath

from .vector_text_render_primitives import TextRenderPrimitives


class VectorTextCacheManager:
    """Manage vector QPainterPath cache entries owned by the canvas."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def prune_qpath_cache(self, preview_obj: Any | None = None) -> None:
        """Remove QPainterPath cache entries for objects no longer in any layer."""
        if not self._canvas._qpath_cache:
            return
        live_ids: set[int] = set()
        for layer in self._canvas._layers:
            if layer.layer_type == "vector":
                for obj in layer.vector_objects:
                    live_ids.add(id(obj))
        if preview_obj is not None:
            live_ids.add(id(preview_obj))
        stale = self._canvas._qpath_cache.keys() - live_ids
        for key in stale:
            del self._canvas._qpath_cache[key]

    def get_cached_qpaths(self, vec: Any) -> list[QPainterPath]:
        """Return cached QPainterPath list for a VectorObject, building if needed."""
        key = id(vec)
        cached = self._canvas._qpath_cache.get(key)
        if cached is not None:
            return cast("list[QPainterPath]", cached)
        qpaths = TextRenderPrimitives.build_vector_qpaths(vec)
        self._canvas._qpath_cache[key] = qpaths
        return qpaths


__all__ = ("VectorTextCacheManager",)
