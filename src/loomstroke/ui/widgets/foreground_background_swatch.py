"""Photoshop-style foreground/background colour swatch for the tool palette."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QHelpEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from loomstroke.ui.translations import TranslationBinder, translate_with_fallback

_DOMAIN = "ColorSwatch"
_REGION_TOOLTIPS = {
    "foreground": "Foreground color (click to change)",
    "background": "Background color (click to change)",
    "swap": "Swap foreground and background colors",
    "reset": "Default colors (black / white)",
}


def _ts(text: str) -> str:
    return translate_with_fallback(_DOMAIN, text)


class ForegroundBackgroundSwatch(QWidget):
    """Two overlapping colour chips with swap and reset hot spots.

    The widget only renders colours and reports which region was clicked;
    colour state lives in :class:`ForegroundBackgroundColors`.
    """

    foreground_requested = Signal()
    background_requested = Signal()
    swap_requested = Signal()
    reset_requested = Signal()

    def __init__(self, side: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("foregroundBackgroundSwatch")
        self.setFixedSize(side, side)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._foreground = QColor(0, 0, 0)
        self._background = QColor(255, 255, 255)
        self._translations = TranslationBinder(_ts)
        self._translations.register(self.setAccessibleName, "Foreground and background colors")

    def refresh_translations(self) -> None:
        self._translations.retranslate()

    def set_colors(self, foreground: QColor, background: QColor) -> None:
        self._foreground = QColor(foreground)
        self._background = QColor(background)
        self.update()

    def colors(self) -> tuple[QColor, QColor]:
        return QColor(self._foreground), QColor(self._background)

    # -- geometry ---------------------------------------------------------
    def _chip_side(self) -> int:
        return max(8, round(self.width() * 0.58))

    def region_rects(self) -> dict[str, QRect]:
        side = self.width()
        chip = self._chip_side()
        corner = side - chip - 2
        return {
            "foreground": QRect(1, 1, chip, chip),
            "background": QRect(side - chip - 1, side - chip - 1, chip, chip),
            "swap": QRect(chip + 2, 0, corner, corner),
            "reset": QRect(0, chip + 2, corner, corner),
        }

    def region_at(self, pos: QPoint) -> str | None:
        rects = self.region_rects()
        for region in ("foreground", "swap", "reset", "background"):
            if rects[region].contains(pos):
                return region
        return None

    # -- events -----------------------------------------------------------
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        region = self.region_at(event.position().toPoint())
        signal = {
            "foreground": self.foreground_requested,
            "background": self.background_requested,
            "swap": self.swap_requested,
            "reset": self.reset_requested,
        }.get(region or "")
        if signal is not None:
            signal.emit()
        event.accept()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            region = self.region_at(event.pos())
            if region is None:
                QToolTip.hideText()
            else:
                QToolTip.showText(event.globalPos(), _ts(_REGION_TOOLTIPS[region]), self)
            return True
        return super().event(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # pragma: no cover - visual only
        rects = self.region_rects()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outline = self.palette().windowText().color()
        outline.setAlpha(170)
        self._paint_chip(painter, rects["background"], self._background, outline)
        self._paint_chip(painter, rects["foreground"], self._foreground, outline)
        self._paint_swap_glyph(painter, rects["swap"], outline)
        self._paint_reset_glyph(painter, rects["reset"], outline)
        painter.end()

    @staticmethod
    def _paint_chip(painter: QPainter, rect: QRect, color: QColor, outline: QColor) -> None:
        if color.alpha() < 255:
            half = max(2, rect.width() // 4)
            for row in range(0, rect.height(), half):
                for col in range(0, rect.width(), half):
                    shade = (
                        QColor(204, 204, 204) if (row + col) // half % 2 else QColor(255, 255, 255)
                    )
                    painter.fillRect(
                        QRect(rect.x() + col, rect.y() + row, half, half).intersected(rect), shade
                    )
        painter.fillRect(rect, color)
        painter.setPen(QPen(outline, 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    @staticmethod
    def _paint_swap_glyph(painter: QPainter, rect: QRect, color: QColor) -> None:
        inset = rect.adjusted(2, 2, -2, -2)
        if inset.width() < 4 or inset.height() < 4:
            return
        left, top, right, bottom = inset.left(), inset.top(), inset.right(), inset.bottom()
        painter.setPen(QPen(color, 1.2))
        painter.drawLine(QPointF(left + 2, top + 1), QPointF(right - 1, top + 1))
        painter.drawLine(QPointF(right - 1, top + 1), QPointF(right - 1, bottom - 2))
        painter.setBrush(color)
        head = 2.5
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(left, top + 1),
                    QPointF(left + head, top + 1 - head),
                    QPointF(left + head, top + 1 + head),
                ]
            )
        )
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(right - 1, bottom),
                    QPointF(right - 1 - head, bottom - head),
                    QPointF(right - 1 + head, bottom - head),
                ]
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _paint_reset_glyph(painter: QPainter, rect: QRect, outline: QColor) -> None:
        mini = max(3, min(rect.width(), rect.height()) // 2)
        back = QRect(rect.left() + mini // 2 + 1, rect.bottom() - mini, mini, mini)
        front = QRect(rect.left() + 1, back.top() - mini // 2, mini, mini)
        painter.setPen(QPen(outline, 1))
        painter.fillRect(back, QColor(255, 255, 255))
        painter.drawRect(back.adjusted(0, 0, -1, -1))
        painter.fillRect(front, QColor(0, 0, 0))
        painter.drawRect(front.adjusted(0, 0, -1, -1))


__all__ = ["ForegroundBackgroundSwatch"]
