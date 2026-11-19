"""Replay selection pixel patches in the backing rectangle used at each history step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .command_layer_access import _ensure_layer_bounds, _layer_offset
from .command_types import LayerTransform, SelectionRect

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

    from .canvas import Canvas


@dataclass(frozen=True, slots=True)
class BackingGeometry:
    bounds: SelectionRect
    transform: LayerTransform | None

    @classmethod
    def capture(cls, layer: Any) -> BackingGeometry:
        x, y = _layer_offset(layer)
        image = layer.image
        return cls((x, y, image.width(), image.height()), getattr(layer, "transform", None))

    def restore_pixels(self, layer: Any, x: int, y: int, patch: QImage) -> None:
        """Keep pixels outside the patch, including any beyond the page edge."""
        from PySide6.QtGui import QPainter  # noqa: PLC0415

        old_x, old_y = _layer_offset(layer)
        left, top, width, height = self.bounds
        # QImage.copy pads out-of-bounds pixels with zeroes when replaying an expansion.
        image = layer.image.copy(left - old_x, top - old_y, width, height)
        if image.isNull():
            raise MemoryError("Could not restore selection backing image")
        with QPainter(image) as painter:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(x - left, y - top, patch)
        layer.image = image
        layer.offset_x, layer.offset_y = left, top
        if hasattr(layer, "transform"):
            layer.transform = self.transform
        if hasattr(layer, "transform_version"):
            layer.transform_version += 1


@dataclass(frozen=True, slots=True)
class BackingExpansion:
    before: BackingGeometry
    after: BackingGeometry

    @classmethod
    def ensure(cls, canvas: Canvas, layer: Any, bounds: SelectionRect) -> BackingExpansion | None:
        before = BackingGeometry.capture(layer)
        _ensure_layer_bounds(canvas, layer, bounds)
        after = BackingGeometry.capture(layer)
        return None if after == before else cls(before, after)
