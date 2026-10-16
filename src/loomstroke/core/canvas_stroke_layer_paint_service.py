"""Layer QImage paint helpers for ``SimpleCanvasService`` strokes."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter


class CanvasStrokeLayerPaintService:
    """Apply paint / erase stroke primitives to layer images."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def paint_erased_masked_stroke(
        self,
        *,
        layer: Any,
        points: list[Any],
        brush_size: int,
        bounds: Any,
        local_bounds: Any,
    ) -> None:
        owner = self._owner
        temp = QImage(bounds.size(), QImage.Format.Format_ARGB32_Premultiplied)
        temp.fill(Qt.GlobalColor.transparent)
        temp_painter = QPainter(temp)
        temp_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        owner._draw_erase_stroke(
            temp_painter,
            points,
            brush_size,
            color=QColor(255, 255, 255),
            offset=bounds,
        )
        temp_painter.end()

        selection_mask = owner._selection_mask
        if selection_mask is None:
            return
        mask_patch = selection_mask.copy(bounds)
        owner._apply_selection_mask_to_image(temp, mask_patch)

        painter = QPainter(cast("QImage", layer.image))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        painter.drawImage(local_bounds.topLeft(), temp)
        painter.end()

    def paint_erased_clear_stroke(
        self,
        *,
        layer: Any,
        points: list[Any],
        brush_size: int,
        offset_x: int,
        offset_y: int,
    ) -> None:
        owner = self._owner
        painter = QPainter(cast("QImage", layer.image))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.translate(-offset_x, -offset_y)
        owner._draw_erase_stroke(
            painter,
            points,
            brush_size,
            color=QColor(0, 0, 0, 0),
        )
        painter.end()

    def paint_into_selection(
        self,
        *,
        layer: Any,
        points: list[Any],
        brush: Any,
        color: QColor,
        bounds: Any,
        local_bounds: Any,
    ) -> None:
        owner = self._owner
        temp = QImage(bounds.size(), QImage.Format.Format_ARGB32_Premultiplied)
        temp.fill(Qt.GlobalColor.transparent)
        temp_painter = QPainter(temp)
        temp_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        owner._draw_paint_stroke(
            temp_painter,
            points,
            brush,
            color,
            offset=bounds,
        )
        temp_painter.end()

        selection_mask = owner._selection_mask
        if selection_mask is None:
            return
        mask_patch = selection_mask.copy(bounds)
        owner._apply_selection_mask_to_image(temp, mask_patch)

        painter = QPainter(cast("QImage", layer.image))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(local_bounds.topLeft(), temp)
        painter.end()

    def paint_into_layer(
        self,
        *,
        layer: Any,
        points: list[Any],
        brush: Any,
        color: QColor,
        offset_x: int,
        offset_y: int,
    ) -> None:
        owner = self._owner
        painter = QPainter(cast("QImage", layer.image))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(-offset_x, -offset_y)
        owner._draw_paint_stroke(painter, points, brush, color)
        painter.end()
