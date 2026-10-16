"""Draw CanvasChangeset shape geometry on low-resolution preview thumbnails."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen

from loomstroke.core.vector_text_render_service import VectorTextRenderService

from .canvas_shapes import build_canvas_shape


def draw_changeset_shapes(
    image: QImage,
    changeset: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    """Use exactly the shape factory used by apply, without changing live state."""

    operations = changeset.get("operations")
    if not isinstance(operations, list):
        return
    shapes = [
        operation
        for operation in operations
        if isinstance(operation, Mapping)
        and str(operation.get("op") or operation.get("kind") or "").strip() == "add_vector_shape"
    ]
    if not shapes:
        return
    canvas = context.get("canvas")
    canvas = canvas if isinstance(canvas, Mapping) else {}
    width = max(1, int(canvas.get("width") or 1024))
    height = max(1, int(canvas.get("height") or 768))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(image.width() / width, image.height() / height)
        for index, operation in enumerate(shapes):
            obj = build_canvas_shape(operation, object_id=f"shape-preview-{index}")
            pen = QPen(QColor(*obj.style.stroke_color))
            pen.setWidthF(obj.style.stroke_width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(
                QBrush(QColor(*obj.style.fill_color))
                if obj.style.fill_enabled and obj.style.fill_color is not None
                else QBrush(Qt.BrushStyle.NoBrush)
            )
            for path in VectorTextRenderService.build_vector_qpaths(obj):
                painter.drawPath(path)
    finally:
        painter.end()
