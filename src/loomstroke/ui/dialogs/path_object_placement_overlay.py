"""Native canvas guide and draggable interval handles for object placement."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .path_object_placement_preview import PathObjectPlacementPreview


class PathObjectPlacementOverlay:
    def __init__(self, dialog, canvas):
        self.dialog = dialog
        self.canvas = canvas
        self.previous = getattr(canvas, "_tool_overlay_fn", None)
        self._dragging = None
        self.preview = PathObjectPlacementPreview(dialog, canvas)
        self._setter = getattr(canvas, "set_tool_overlay_fn", None)
        if callable(self._setter):
            self._setter(self.paint)
        surfaces = (
            canvas,
            getattr(canvas, "_overlay_surface", None),
            getattr(canvas, "_native_surface", None),
            getattr(canvas, "legacy_canvas", None),
        )
        self.event_surfaces = tuple(surface for surface in surfaces if isinstance(surface, QWidget))
        for surface in self.event_surfaces:
            surface.installEventFilter(dialog)

    def close(self):
        self.preview.close()
        for surface in self.event_surfaces:
            surface.removeEventFilter(self.dialog)
        if callable(self._setter) and getattr(self.canvas, "_tool_overlay_fn", None) == self.paint:
            self._setter(self.previous)
        self.canvas.update()

    def refresh(self):
        self.preview.refresh()

    def handle_points(self):
        track = self.dialog.track()
        if track is None:
            return {}
        viewport = self.canvas.viewport
        start = self.dialog.start_spin.value() / 100
        end = self.dialog.end_spin.value() / 100
        return {
            "start": QPointF(*viewport.canvas_to_widget(*self._xy(track.at_fraction(start)[0]))),
            "end": QPointF(*viewport.canvas_to_widget(*self._xy(track.at_fraction(end)[0]))),
        }

    @staticmethod
    def _xy(point):
        return point.x(), point.y()

    def event_filter(self, event, watched=None):
        if not isinstance(event, QMouseEvent):
            return False
        kind = event.type()
        surface = watched or self.canvas
        local_point = (
            QPointF(surface.mapTo(self.canvas, event.position().toPoint()))
            if surface is not self.canvas
            else event.position()
        )
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            for name, handle_point in self.handle_points().items():
                if (handle_point - local_point).manhattanLength() <= 13:
                    self._dragging = name
                    return True
        if kind == QEvent.Type.MouseMove and self._dragging:
            track = self.dialog.track()
            if track is not None:
                x, y = self.canvas.viewport.widget_to_canvas(local_point.x(), local_point.y())
                self.dialog.set_endpoint(self._dragging, track.project(QPointF(x, y)))
            return True
        if kind == QEvent.Type.MouseButtonRelease and self._dragging:
            self._dragging = None
            return True
        return False

    def paint(self, painter: QPainter):
        if callable(self.previous):
            self.previous(painter)
        self.preview.paint(painter)
        track = self.dialog.track()
        if track is None:
            return
        painter.save()
        transform = painter.worldTransform()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#8d9aaa"), 1, Qt.PenStyle.DashLine))
        painter.drawPolyline(
            QPolygonF([transform.map(track.at_fraction(i / 128)[0]) for i in range(129)])
        )
        painter.setPen(QPen(QColor("#42baff"), 2))
        painter.drawPolyline(
            QPolygonF(
                [
                    transform.map(track.frame_at(track.interval_length * i / 128).map(QPointF()))
                    for i in range(129)
                ]
            )
        )
        for frame in self.preview.frames:
            point = transform.map(frame.map(QPointF()))
            painter.setBrush(QColor("#42baff"))
            painter.drawEllipse(point, 3, 3)
        for name, point in self.handle_points().items():
            painter.setBrush(QColor("#44d89e" if name == "start" else "#ffb454"))
            painter.drawEllipse(point, 6, 6)
        painter.restore()
