"""Transient canvas overlay for the inpaint pen's brush mask."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from loomstroke.ui._dock_safe import is_valid

_MASK_COLOR = QColor(255, 0, 170, 120)


class RepairMaskPreview:
    """Draw the pending mask in canvas coordinates without editing a layer."""

    def __init__(self) -> None:
        self._widget: Any = None
        self._path = QPainterPath()
        self._first_point: QPointF | None = None
        self._size = 1

    def begin(self, host: Any, point: tuple[float, float], size: int) -> None:
        self.clear()
        self._first_point = QPointF(*point)
        self._path = QPainterPath(self._first_point)
        self._size = size
        ui = getattr(host, "ui", None)
        getter = getattr(ui, "get_main_window", None)
        if not callable(getter):
            return
        try:
            window = getter()
        except Exception:
            return
        widget = getattr(window, "canvas", None)
        if callable(getattr(widget, "set_tool_overlay_fn", None)):
            self._widget = widget
            widget.set_tool_overlay_fn(self.paint)

    def extend(self, points: Iterable[tuple[float, float]]) -> None:
        changed = False
        for point in points:
            self._path.lineTo(QPointF(*point))
            changed = True
        if changed and is_valid(self._widget):
            self._widget.update()

    def set_size(self, size: int) -> None:
        self._size = size
        if is_valid(self._widget):
            self._widget.update()

    def clear(self) -> None:
        if is_valid(self._widget):
            self._widget.set_tool_overlay_fn(None)
        self._widget = None
        self._first_point = None
        self._path = QPainterPath()

    def paint(self, painter: QPainter) -> None:
        if self._first_point is None:
            return
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(_MASK_COLOR, self._size)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            if self._path.elementCount() > 1:
                painter.drawPath(self._path)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(_MASK_COLOR)
                painter.drawEllipse(self._first_point, self._size / 2, self._size / 2)
        finally:
            painter.restore()
