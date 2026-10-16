"""Transient screen-space frame targets and direction feedback."""

import math
from itertools import pairwise

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class LinkOverlay(QWidget):
    def __init__(self, picker, canvas):
        super().__init__(canvas)
        self.picker = picker
        self._setter = getattr(canvas, "set_tool_overlay_fn", None)
        self._previous = getattr(canvas, "_tool_overlay_fn", None)
        self._native_callback = self.paint_native
        if callable(self._setter):
            self._setter(self._native_callback)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)
        self.refresh()
        self.show()

    def refresh(self):
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.update()
        if callable(self._setter):
            self.parentWidget().update()

    def hide(self):
        if (
            callable(self._setter)
            and getattr(self.parentWidget(), "_tool_overlay_fn", None) == self._native_callback
        ):
            self._setter(self._previous)
        super().hide()

    def paint_native(self, painter):
        if self._previous is not None:
            self._previous(painter)
        painter.save()
        transform = painter.worldTransform()
        painter.resetTransform()
        try:
            self.draw(painter, lambda x, y: transform.map(QPointF(x, y)))
        finally:
            painter.restore()

    def paintEvent(self, event):
        del event
        if callable(self._setter):
            return
        painter = QPainter(self)
        viewport = self.parentWidget().viewport
        self.draw(painter, lambda x, y: QPointF(*viewport.canvas_to_widget(x, y)))
        painter.end()

    def draw(self, painter, map_point):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#38a9ff"), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        centers = {}
        for number, (key, hit) in enumerate(self.picker.visible_frames(), 1):
            x0, y0, x1, y1 = hit.rect
            points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            angle = math.radians(self.picker.tool._payload_rotation(hit.payload))
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            polygon = []
            for px, py in points:
                dx, dy = px - cx, py - cy
                x, y = cx + dx * math.cos(angle) - dy * math.sin(angle), cy + dx * math.sin(
                    angle
                ) + dy * math.cos(angle)
                polygon.append(map_point(x, y))
            painter.drawPolygon(QPolygonF(polygon))
            center = map_point(cx, cy)
            centers[key] = center
            if self.picker.ordered:
                painter.drawText(center, str(number))
        for index, (start, end) in enumerate(pairwise(self.picker.feedback), 1):
            if start not in centers and end not in centers:
                continue
            a = centers.get(
                start, centers.get(end) - QPointF(50, 30) if end in centers else QPointF()
            )
            b = centers.get(end, a + QPointF(50, 30))
            if start not in centers or end not in centers:
                painter.drawText(a, f"{index} → {index + 1}")
            painter.drawLine(a, b)
            angle = math.atan2(b.y() - a.y(), b.x() - a.x())
            for turn in (-0.5, 0.5):
                painter.drawLine(
                    b, b - QPointF(12 * math.cos(angle + turn), 12 * math.sin(angle + turn))
                )
