"""Accessible checkbox semantics with a compact on/off switch presentation."""

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QCheckBox, QWidget


class LayerStyleSwitch(QCheckBox):
    def __init__(self, title: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAccessibleName(title)
        self.setFixedSize(38, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        track = palette.highlight().color() if self.isChecked() else palette.mid().color()
        if not self.isEnabled():
            painter.setOpacity(0.45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(1, 3, 36, 18), 9, 9)
        painter.setBrush(palette.color(QPalette.ColorRole.Base))
        painter.drawEllipse(QRectF(21 if self.isChecked() else 3, 5, 14, 14))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(palette.highlight().color())
            painter.drawRoundedRect(QRectF(0.5, 0.5, 37, 23), 5, 5)
