"""Swap a raster layer's whole image buffer, undoably (CT-18 Reload / Relink / Replace).

Layer identity, offset/transform, mask, clipping and opacity are untouched;
only ``layer.image`` changes, and only the consumer layer's texture is bumped.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .command_types import Command
from .commands_layer_lookup import find_layer

__all__ = ["ReplaceLayerImageCommand"]


class ReplaceLayerImageCommand(Command):
    def __init__(self, layer_id: str, image: QImage, *, merge_key: str | None = None) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._image = image
        self._previous: QImage | None = None

    def apply(self, canvas: Any) -> bool:
        layer = find_layer(canvas, self.layer_id)
        if layer is None or not hasattr(layer, "image"):
            return False
        self._previous = layer.image
        layer.image = self._image
        self._executed = True
        self._bump(canvas, layer)
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        layer = find_layer(canvas, self.layer_id)
        if layer is None:
            return False
        layer.image = self._previous
        self._executed = False
        self._bump(canvas, layer)
        return True

    @staticmethod
    def _bump(canvas: Any, layer: Any) -> None:
        image = getattr(layer, "image", None)
        width = image.width() if isinstance(image, QImage) else 0
        height = image.height() if isinstance(image, QImage) else 0
        if hasattr(layer, "texture_version"):
            layer.texture_version += 1
        mark_dirty = getattr(layer, "mark_dirty", None)
        if callable(mark_dirty):
            mark_dirty(0, 0, width, height)
        for name in ("_invalidate_layer_cache", "_recompose"):
            hook = getattr(canvas, name, None)
            if callable(hook):
                hook()
        emit = getattr(canvas, "_emit_layer_content_changed", None)
        if callable(emit):
            emit(layer, QRect(0, 0, width, height))
