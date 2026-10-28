"""Schematic trim / bleed / media / crop-mark preview (CT-11 P4, spec 11 §5.2).

Draws a :class:`PrintPageGeometry` to scale inside the widget: the media
(paper) outline, the bleed area (= page raster), the trim box and the
crop marks, all in the same point coordinates the PDF will use.  Pure
display — no document pixels are touched and nothing is persisted.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.print_page_geometry import PrintPageGeometry
from .print_margin_painter import draw_print_margin

__all__ = ["PrintGeometryPreview"]

_PADDING_PX = 8.0


class PrintGeometryPreview(QWidget):
    """Fit-to-widget rendering of one page's print geometry."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._geometry: PrintPageGeometry | None = None
        self.setMinimumSize(120, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def geometry_model(self) -> PrintPageGeometry | None:
        return self._geometry

    def set_geometry(self, geometry: PrintPageGeometry | None) -> None:
        if geometry == self._geometry:
            return
        self._geometry = geometry
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(180, 220)

    # -- painting ------------------------------------------------------------

    def _scale(self) -> tuple[float, float, float]:
        """(scale, x offset, y offset) mapping media points to widget pixels."""
        assert self._geometry is not None
        _mx, _my, media_w, media_h = self._geometry.media_box_pt
        avail_w = max(1.0, self.width() - _PADDING_PX * 2)
        avail_h = max(1.0, self.height() - _PADDING_PX * 2)
        scale = min(avail_w / max(media_w, 1e-6), avail_h / max(media_h, 1e-6))
        offset_x = (self.width() - media_w * scale) / 2.0
        offset_y = (self.height() - media_h * scale) / 2.0
        return scale, offset_x, offset_y

    def _map_rect(self, box: tuple[float, float, float, float]) -> QRectF:
        scale, ox, oy = self._scale()
        x, y, w, h = box
        return QRectF(ox + x * scale, oy + y * scale, w * scale, h * scale)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._geometry is None:
                painter.fillRect(self.rect(), self.palette().window())
                return
            geometry = self._geometry
            painter.fillRect(self.rect(), self.palette().window())
            media = self._map_rect(geometry.media_box_pt)
            painter.fillRect(media, QColor(255, 255, 255))
            painter.setPen(QPen(QColor(140, 140, 140), 1.0))
            painter.drawRect(media)
            # Bleed area (= canvas raster) in light grey, trim box in white
            # with a red outline — the printer's cut line.
            painter.fillRect(self._map_rect(geometry.bleed_box_pt), QColor(225, 225, 225))
            trim = self._map_rect(geometry.trim_box_pt)
            painter.fillRect(trim, QColor(250, 250, 250))
            trim_pen = QPen(QColor(200, 40, 40), 1.0)
            if not geometry.has_bleed:
                trim_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(trim_pen)
            painter.drawRect(trim)
            painter.setPen(QPen(QColor(30, 30, 30), 1.0))
            scale, ox, oy = self._scale()
            for mark in geometry.marks:
                painter.drawLine(
                    round(ox + mark.x1 * scale),
                    round(oy + mark.y1 * scale),
                    round(ox + mark.x2 * scale),
                    round(oy + mark.y2 * scale),
                )
            for cx, cy, radius in geometry.circle_marks:
                painter.drawEllipse(
                    QRectF(
                        ox + (cx - radius) * scale,
                        oy + (cy - radius) * scale,
                        radius * 2 * scale,
                        radius * 2 * scale,
                    )
                )
            painter.save()
            painter.translate(ox, oy)
            painter.scale(scale, scale)
            draw_print_margin(painter, geometry.margin_info)
            painter.restore()
        finally:
            painter.end()
