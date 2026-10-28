"""Draw derived marks only on an export device, never on document rasters."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ..core.print_page_geometry import PrintPageGeometry


def draw_print_marks(painter: QPainter, geometry: PrintPageGeometry) -> None:
    if not geometry.marks and not geometry.circle_marks:
        return
    painter.save()
    try:
        pen = QPen()
        options = geometry.mark_options
        pen.setWidthF(options.width_pt)
        if options.color == "k_only":
            pen.setColor(QColor.fromCmykF(0, 0, 0, 1))
        elif options.color == "registration":
            pen.setColor(QColor.fromCmykF(1, 1, 1, 1))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for mark in geometry.marks:
            painter.drawLine(QPointF(mark.x1, mark.y1), QPointF(mark.x2, mark.y2))
        for cx, cy, radius in geometry.circle_marks:
            painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
    finally:
        painter.restore()
