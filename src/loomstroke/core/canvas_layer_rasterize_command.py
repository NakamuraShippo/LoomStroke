"""An undoable replacement of a vector layer's content with prepared pixels."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .command_types import Command
from .native_cmyk.buffer import NativeCmykBuffer
from .native_cmyk.pixels import NativeCmykPixels


def _layer_selection(selection: Any, layer_id: str) -> bool:
    return isinstance(selection, Mapping) and selection.get("layer_id") in (None, layer_id)


class RasterizeLayerCommand(Command):
    """Keep the layer identity and placement while switching its content type."""

    def __init__(
        self, canvas: Any, layer: Any, image: QImage, native: NativeCmykPixels | None
    ) -> None:
        super().__init__()
        self.layer_id = layer.id
        self._objects = tuple(layer.vector_objects)
        self._original_image = layer.image
        self._original_native = layer.native_cmyk
        selection = getattr(canvas, "_vector_selection", None)
        self._selection = selection if _layer_selection(selection, self.layer_id) else None
        self._image = QImage(image)
        self._native = native

    @property
    def retained_bytes(self) -> int:
        total = self._image.sizeInBytes()
        if self._original_image is not None:
            total += self._original_image.sizeInBytes()
        for pixels in (self._native, self._original_native):
            if pixels is not None:
                total += pixels.inks.nbytes + pixels.alpha.nbytes
        return total

    def install_raster(self, canvas: Any) -> Any | None:
        layer = canvas._get_layer_by_id(self.layer_id)
        if layer is None or layer.layer_type != "vector":
            return None
        image = QImage(self._image)
        native = NativeCmykBuffer(self._native) if self._native is not None else None
        layer.image = image
        layer.native_cmyk = native
        layer.layer_type = "raster"
        layer.vector_objects = []
        layer.texture_version += 1
        if _layer_selection(getattr(canvas, "_vector_selection", None), self.layer_id):
            canvas._vector_selection = None
        self._executed = True
        return layer

    def restore_vector(self, canvas: Any) -> Any | None:
        layer = canvas._get_layer_by_id(self.layer_id)
        if not self._executed or layer is None or layer.layer_type != "raster":
            return None
        layer.image = self._original_image
        layer.native_cmyk = self._original_native
        layer.layer_type = "vector"
        layer.vector_objects = list(self._objects)
        layer.texture_version += 1
        selection = getattr(canvas, "_vector_selection", None)
        if selection is None or _layer_selection(selection, self.layer_id):
            canvas._vector_selection = self._selection
        self._executed = False
        return layer

    @staticmethod
    def notify(canvas: Any, layer: Any) -> None:
        canvas._invalidate_layer_cache()
        canvas._recompose()
        rect = QRect(0, 0, canvas._width, canvas._height)
        canvas._emit_layer_content_changed(layer, rect, {"command": "rasterize"})

    def apply(self, canvas: Any) -> bool:
        layer = self.install_raster(canvas)
        if layer is None:
            return False
        self.notify(canvas, layer)
        return True

    def revert(self, canvas: Any) -> bool:
        layer = self.restore_vector(canvas)
        if layer is None:
            return False
        self.notify(canvas, layer)
        return True
