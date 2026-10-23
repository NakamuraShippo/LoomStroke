"""Non-printing interval guides using the existing native tool-overlay port."""

import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .path_text_controls import ts


class PathTextOverlay(QWidget):
    def __init__(self, controller, canvas):
        super().__init__(canvas)
        self.controller = controller
        self.setter = getattr(canvas, "set_tool_overlay_fn", None)
        self.previous = getattr(canvas, "_tool_overlay_fn", None)
        self.callback = self.paint_native
        if callable(self.setter):
            self.setter(self.callback)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(80)
        # The canvas port owns visible painting. Showing this extra child on
        # Windows would cover the native surface with Qt backing-store pixels.
        if not callable(self.setter):
            self.show()

    def refresh(self):
        if not callable(self.setter):
            self.setGeometry(self.parentWidget().rect())
            self.raise_()
            self.update()
        self.controller.refresh_controls()
        if callable(self.setter):
            self.parentWidget().update()

    def close_overlay(self):
        self.timer.stop()
        if (
            callable(self.setter)
            and getattr(self.parentWidget(), "_tool_overlay_fn", None) == self.callback
        ):
            self.setter(self.previous)
        self.hide()
        self.deleteLater()
        self.parentWidget().update()

    def paint_native(self, painter):
        if callable(self.previous):
            self.previous(painter)
        painter.save()
        transform = painter.worldTransform()
        painter.resetTransform()
        self.draw(painter, lambda p: transform.map(p))
        painter.restore()

    def paintEvent(self, event):
        del event
        if callable(self.setter):
            return
        painter = QPainter(self)
        viewport = self.parentWidget().viewport
        self.draw(painter, lambda p: QPointF(*viewport.canvas_to_widget(p.x(), p.y())))
        painter.end()

    def draw(self, painter, map_point):
        if self.controller.nodes.active:
            self.controller.nodes.draw(painter, map_point)
            return
        target = self.controller.target()
        if target is None:
            return
        track, rotation = self.controller.track_and_rotation(target[0])
        if track is None:
            return

        def mapped(p):
            return map_point(rotation.map(p))

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#929ba8"), 1, Qt.PenStyle.DashLine))
        painter.drawPolyline(QPolygonF([mapped(track.at_fraction(i / 128)[0]) for i in range(129)]))
        painter.setPen(QPen(QColor("#42baff"), 2))
        painter.drawPolyline(
            QPolygonF(
                [
                    mapped(track.frame_at(track.interval_length * i / 128).map(QPointF()))
                    for i in range(129)
                ]
            )
        )
        for name, point in self.controller.handle_points(track, mapped).items():
            painter.setBrush(QColor("#44d89e" if name == "start" else "#ffb454"))
            painter.drawEllipse(point, 6, 6)
            painter.drawText(point + QPointF(9, -9), ts("Start" if name == "start" else "End"))
        mid = track.interval_length * 0.5
        a = mapped(track.frame_at(max(0, mid - 4)).map(QPointF()))
        b = mapped(track.frame_at(min(track.interval_length, mid + 4)).map(QPointF()))
        angle = math.atan2(b.y() - a.y(), b.x() - a.x())
        for turn in (-0.5, 0.5):
            painter.drawLine(b, b - QPointF(9 * math.cos(angle + turn), 9 * math.sin(angle + turn)))
