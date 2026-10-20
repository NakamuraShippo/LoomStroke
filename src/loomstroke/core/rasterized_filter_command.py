"""Convert a confirmed vector/text candidate and its filter in one undo step."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QRect

from .filter_pen.command import FilterPenStrokeCommand
from .filter_pen.native_pixels import write_native_tiles
from .filter_pen.snapshot import FilterPenSnapshot
from .native_cmyk.buffer import NativeCmykBuffer
from .rasterized_filter_target import RasterizedFilterTarget


class RasterizedFilterCommand(FilterPenStrokeCommand):
    def __init__(self, snapshot: FilterPenSnapshot, target: RasterizedFilterTarget) -> None:
        super().__init__(snapshot)
        self.target = target
        self._after: Any = None

    @property
    def retained_bytes(self) -> int:
        total = super().retained_bytes
        for layer in (self.target.raster, self._after):
            if layer is not None:
                total += layer.image.sizeInBytes()
                if layer.native_cmyk is not None:
                    total += layer.native_cmyk.inks.nbytes + layer.native_cmyk.alpha.nbytes
        return total

    def apply(self, canvas: Any) -> bool:
        layer = canvas._get_layer_by_id(self.layer_id)
        if layer is not self.target.source:
            return False
        if not self._ever_applied and not self.target.valid(canvas):
            return False
        if self._after is None:
            source = self.target.raster
            prepared = replace(
                source,
                image=source.image.copy(),
                native_cmyk=(
                    NativeCmykBuffer(source.native_cmyk.snapshot())
                    if source.native_cmyk is not None
                    else None
                ),
            )
            if self._native:
                write_native_tiles(prepared, self.tiles, before=False)
            else:
                self._write_rgb(prepared, self.tiles, before=False)
            self._after = prepared
            self.tiles.clear()
        self._executed = True
        for key in self.target.FIELDS:
            value = getattr(self._after, key)
            if key == "image":
                value = value.copy()
            elif key == "native_cmyk" and value is not None:
                value = NativeCmykBuffer(value.snapshot())
            elif key == "vector_objects":
                value = []
            setattr(layer, key, value)
        selection = getattr(canvas, "_vector_selection", None)
        if selection and selection.get("layer_id") in (None, layer.id):
            canvas._vector_selection = None
        self._ever_applied = True
        self._notify(canvas, layer)
        return True

    def revert(self, canvas: Any) -> bool:
        layer = canvas._get_layer_by_id(self.layer_id)
        if not self._executed or layer is not self.target.source:
            return False
        for key, value in self.target.before.items():
            setattr(layer, key, deepcopy(value) if key == "vector_objects" else value)
        canvas._vector_selection = deepcopy(self.target.selection)
        self._notify(canvas, layer)
        self._executed = False
        return True

    @staticmethod
    def _notify(canvas: Any, layer: Any) -> None:
        layer.texture_version += 1
        rect = QRect(0, 0, canvas._width, canvas._height)
        canvas._recompose_region(rect)
        canvas._emit_layer_content_changed(layer, rect, {"command": "rasterize_filter"})
