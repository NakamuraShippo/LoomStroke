"""Draw clipped single-line information and process swatches on export devices."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter

from ..core.print_margin_models import PrintMarginGeometry


def draw_print_margin(painter: QPainter, info: PrintMarginGeometry | None) -> None:
    if info is None:
        return
    painter.save()
    try:
        for rect, cmyk in info.swatches:
            painter.fillRect(QRectF(*rect), QColor.fromCmykF(*cmyk))
        if info.text_rect_pt is not None and info.text:
            rect = QRectF(*info.text_rect_pt)
            font = painter.font()
            font.setPixelSize(6)  # PDF device resolution is 72: six point text.
            painter.setFont(font)
            painter.setPen(QColor.fromCmykF(0, 0, 0, 1))
            painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
            text = QFontMetricsF(font, painter.device()).elidedText(
                info.text, Qt.TextElideMode.ElideMiddle, rect.width()
            )
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
    finally:
        painter.restore()
