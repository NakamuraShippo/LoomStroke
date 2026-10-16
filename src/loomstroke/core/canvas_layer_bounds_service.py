"""Layer backing-image bounds helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from .command_types import LayerTransform


class CanvasLayerBoundsService:
    """Expand raster layer backing images while preserving canvas-space offsets."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def ensure_layer_bounds(
        self,
        layer_id: str | None,
        bounds: tuple[int, int, int, int],
    ) -> None:
        owner = self._owner
        if layer_id:
            layer = owner._get_layer_by_id(layer_id)
        elif owner._layers and 0 <= owner._active_index < len(owner._layers):
            # Nested active layers are addressed by id; the index would give
            # their ancestor group.
            layer = owner._get_active_layer()
        else:
            layer = None
        if layer is None:
            return
        x, y, w, h = bounds
        if w <= 0 or h <= 0:
            return
        self.ensure_layer_image_bounds(layer, QRect(int(x), int(y), int(w), int(h)))

    def ensure_layer_image_bounds(self, layer: Any, rect: QRect) -> None:
        if getattr(layer, "layer_type", "raster") == "vector" or layer.image is None:
            return
        if rect.isEmpty():
            return
        offset_x = int(layer.offset_x)
        offset_y = int(layer.offset_y)
        layer_rect = QRect(offset_x, offset_y, layer.image.width(), layer.image.height())
        canvas_rect = QRect(0, 0, self._owner._width, self._owner._height)
        target_rect = layer_rect.united(rect).united(canvas_rect)
        if target_rect == layer_rect:
            return
        new_image = QImage(
            target_rect.width(),
            target_rect.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        new_image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(new_image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(offset_x - target_rect.x(), offset_y - target_rect.y(), layer.image)
        painter.end()
        from .comic_panels.stroke_upload import expand_blank_source  # noqa: PLC0415

        expand_blank_source(layer, layer.image, new_image)
        layer.image = new_image
        layer.offset_x = int(target_rect.x())
        layer.offset_y = int(target_rect.y())
        self._sync_transform_after_expansion(
            layer,
            shift_x=offset_x - int(target_rect.x()),
            shift_y=offset_y - int(target_rect.y()),
        )

    @staticmethod
    def _sync_transform_after_expansion(layer: Any, *, shift_x: int, shift_y: int) -> None:
        """Keep ``layer.transform`` pointing at the same canvas pixels.

        Expanding the backing image moves the local origin by
        ``(shift_x, shift_y)`` (old content is redrawn at that local
        position).  Pixel painting follows ``offset_x/offset_y``, but the
        wgpu compositor places a layer with a non-identity ``transform`` by
        its affine translation, so the translation has to move with the
        origin or the layer (and every later stroke) renders displaced.
        """
        if shift_x == 0 and shift_y == 0:
            return
        transform = getattr(layer, "transform", None)
        if transform is None or transform == LayerTransform.identity():
            return
        affine = transform.to_affine()
        new_tx = float(transform.tx) - (affine.a * shift_x + affine.b * shift_y)
        new_ty = float(transform.ty) - (affine.c * shift_x + affine.d * shift_y)
        layer.transform = transform.with_translation(new_tx, new_ty)
        if hasattr(layer, "transform_version"):
            layer.transform_version = int(getattr(layer, "transform_version", 0)) + 1
