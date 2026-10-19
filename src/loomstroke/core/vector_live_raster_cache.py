"""Bounded live-edit raster reuse, with owned keys for mutable document data."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from typing import Any

from PySide6.QtGui import QImage


class VectorLiveRasterCache:
    MAX_BYTES = 64 * 1024 * 1024
    MAX_LAYERS = 8

    def __init__(self) -> None:
        self._entries: OrderedDict[str, tuple[Any, QImage]] = OrderedDict()

    def render(
        self,
        canvas: Any,
        layer: Any,
        projected: Any,
        scale: int,
        region: Any,
        *,
        coverage_only: bool = False,
    ) -> QImage | None:
        key = (
            canvas._width,
            canvas._height,
            getattr(canvas, "_dpi", None),
            scale,
            region,
            getattr(layer, "texture_version", 0),
            layer.vector_objects,
            projected,
            getattr(canvas, "_vector_preview", None),
            getattr(canvas, "_vector_preview_layer_id", None),
            getattr(canvas, "_vector_selection", None),
            getattr(layer, "transform", None),
            coverage_only,
        )
        identifier = str(layer.id)
        previous = self._entries.get(identifier)
        if previous is not None and previous[0] == key:
            self._entries.move_to_end(identifier)
            return QImage(previous[1])
        rasterize = (
            canvas._rasterize_vector_coverage_layer
            if coverage_only
            else canvas._rasterize_vector_layer
        )
        image: QImage | None = (
            rasterize(layer, scale=scale, region=region)
            if scale > 1 or region is not None
            else rasterize(layer)
        )
        self._entries.pop(identifier, None)
        if image is not None and image.sizeInBytes() <= self.MAX_BYTES:
            self._entries[identifier] = deepcopy(key), QImage(image)
            while (
                len(self._entries) > self.MAX_LAYERS
                or sum(entry[1].sizeInBytes() for entry in self._entries.values()) > self.MAX_BYTES
            ):
                self._entries.popitem(last=False)
        return image
