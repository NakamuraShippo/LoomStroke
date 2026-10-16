"""Inform the user while an authoritative native preview is being prepared."""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor

from loomstroke.ui.translations import translate_with_fallback


class NativeCmykPendingOverlay:
    @staticmethod
    def paint(host, painter):
        projection = getattr(
            getattr(host, "_native_presenter", None), "_native_cmyk_projection", None
        )
        if projection is None or not projection.pending:
            return
        painter.save()
        painter.resetTransform()
        rect = QRect(12, 12, min(340, max(0, host.width() - 24)), 36)
        painter.fillRect(rect, QColor(28, 31, 38, 230))
        painter.setPen(QColor(238, 242, 248))
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignCenter,
            translate_with_fallback("NativeCmyk", "Preparing CMYK preview..."),
        )
        painter.restore()
