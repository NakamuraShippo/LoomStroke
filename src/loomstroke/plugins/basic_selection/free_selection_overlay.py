"""Draw a lightweight contour preview without mutating the selection mask."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from loomstroke.ui._dock_safe import is_valid


class FreeSelectionOverlay:
    def __init__(self) -> None:
        self.path = QPainterPath()
        self._widget: Any = None

    def show(self, host: Any, path: QPainterPath) -> None:
        self.path = path
        if not is_valid(self._widget):
            self._widget = None
            ui = getattr(host, "ui", None)
            window = getattr(ui, "get_main_window", lambda: None)()
            widget = getattr(window, "canvas", None)
            if callable(getattr(widget, "set_tool_overlay_fn", None)):
                self._widget = widget
                widget.set_tool_overlay_fn(self.paint)
        if is_valid(self._widget):
            self._widget.update()

    def clear(self) -> None:
        self.path = QPainterPath()
        if is_valid(self._widget):
            self._widget.set_tool_overlay_fn(None)
            self._widget.update()
        self._widget = None

    def paint(self, painter: QPainter) -> None:
        painter.save()
        try:
            pen = QPen(QColor("white"), 1.0, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path)
            pen.setColor(QColor("black"))
            pen.setDashOffset(4.0)
            painter.setPen(pen)
            painter.drawPath(self.path)
        finally:
            painter.restore()
