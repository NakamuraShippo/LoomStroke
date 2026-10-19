"""Non-printing width handles, kept a fixed size in logical widget pixels."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QTransform

from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.translations import translated_literal_domain


class VectorWidthOverlay:
    def __init__(self) -> None:
        self._widget: Any = None
        self.handles: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def show(self, host: Any, geometry: Any, profile: Any) -> None:
        self.handles = []
        for t, _scale in profile.curve or ((0.0, 1.0), (1.0, 1.0)):
            center, normal = geometry.at(t)
            half = profile.width_at(t) * 0.5
            self.handles.append(
                (
                    (center[0] - normal[0] * half, center[1] - normal[1] * half),
                    (center[0] + normal[0] * half, center[1] + normal[1] * half),
                )
            )
        if not is_valid(self._widget):
            window = getattr(getattr(host, "ui", None), "get_main_window", lambda: None)()
            widget = getattr(window, "canvas", None)
            if callable(getattr(widget, "set_tool_overlay_fn", None)):
                self._widget = widget
                widget.set_tool_overlay_fn(self.paint)
        if is_valid(self._widget):
            self._widget.update()

    def clear(self) -> None:
        self.handles = []
        if is_valid(self._widget):
            self._widget.set_tool_overlay_fn(None)
            self._widget.update()
        self._widget = None

    def paint(self, painter: QPainter) -> None:
        transform = painter.transform()
        painter.save()
        try:
            painter.setTransform(QTransform())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            hint = translated_literal_domain(
                "VectorTools",
                "Drag to change width; Alt-click a stop to remove it; Esc cancels.",
                use_fallback=True,
            )
            box = painter.fontMetrics().boundingRect(hint).adjusted(-6, -4, 6, 4)
            box.moveTopLeft(painter.viewport().topLeft() + QPointF(12, 12).toPoint())
            painter.fillRect(box, QColor(20, 20, 20, 220))
            painter.setPen(QColor("white"))
            painter.drawText(box, hint)
            painter.setPen(QPen(QColor("white"), 3))
            for start, end in self.handles:
                painter.drawLine(transform.map(QPointF(*start)), transform.map(QPointF(*end)))
            painter.setPen(QPen(QColor(160, 40, 160), 1.5))
            painter.setBrush(QColor("white"))
            for start, end in self.handles:
                a, b = transform.map(QPointF(*start)), transform.map(QPointF(*end))
                painter.drawLine(a, b)
                for point in (a, b):
                    painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))
        finally:
            painter.restore()
