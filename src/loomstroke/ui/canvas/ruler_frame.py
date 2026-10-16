"""Container widget that adds rulers around the canvas."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QWidget

from ...platform.settings_manager import SettingsManager
from .guide_controller import GuideController
from .ruler_widget import RulerWidget


class RulerCornerWidget(QFrame):
    """Top-left corner widget used to drag and reset ruler origin."""

    def __init__(
        self, canvas: QWidget, guide_controller: GuideController, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self._guide_controller = guide_controller
        self._dragging = False
        self.setFrameStyle(QFrame.Shape.Panel | QFrame.Shadow.Sunken)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self.grabMouse()
        self._update_origin(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            return
        self._update_origin(event, persist=False)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._dragging:
            self._dragging = False
            self.releaseMouse()
            self._update_origin(event, persist=True)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._guide_controller.reset_ruler_origin()

    def _update_origin(self, event: QMouseEvent, *, persist: bool = True) -> None:
        canvas_pos = self._canvas_pos_from_global(event.globalPosition().toPoint())
        self._guide_controller.set_ruler_origin(canvas_pos, persist=persist)

    def _canvas_pos_from_global(self, global_pos: Any) -> QPointF:
        widget_pos = self._canvas.mapFromGlobal(global_pos)
        viewport = getattr(self._canvas, "viewport", None)
        if viewport is None:
            return QPointF(0.0, 0.0)
        canvas_xy = viewport.widget_to_canvas(widget_pos.x(), widget_pos.y())
        return QPointF(float(canvas_xy[0]), float(canvas_xy[1]))


class RulerCanvasFrame(QWidget):
    """Wraps a CanvasWidget with horizontal and vertical rulers."""

    def __init__(
        self,
        *,
        canvas: QWidget,
        guide_controller: GuideController,
        settings: SettingsManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self._guide_controller = guide_controller
        self._settings = settings
        self._thickness = int(self._settings.get_int("view/ruler_thickness_px", 18))

        self._horizontal = RulerWidget(
            orientation="horizontal",
            canvas=self._canvas,
            guide_controller=self._guide_controller,
            thickness_px=self._thickness,
            parent=self,
        )
        self._vertical = RulerWidget(
            orientation="vertical",
            canvas=self._canvas,
            guide_controller=self._guide_controller,
            thickness_px=self._thickness,
            parent=self,
        )
        self._corner = RulerCornerWidget(self._canvas, self._guide_controller, parent=self)
        self._corner.setFixedSize(self._thickness, self._thickness)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._corner, 0, 0)
        layout.addWidget(self._horizontal, 0, 1)
        layout.addWidget(self._vertical, 1, 0)
        layout.addWidget(self._canvas, 1, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(1, 1)
        self.setLayout(layout)

        self._guide_controller.set_ruler_update_callback(self.update_rulers)
        self.set_rulers_visible(bool(self._settings.get_bool("view/rulers_visible", False)))
        self._attach_cursor_tracking()

    def set_rulers_visible(self, visible: bool) -> None:
        self._horizontal.setVisible(visible)
        self._vertical.setVisible(visible)
        self._corner.setVisible(visible)
        self.updateGeometry()
        self.update()

    def update_rulers(self) -> None:
        self._horizontal.update()
        self._vertical.update()
        self._corner.update()

    def set_thickness(self, thickness_px: int) -> None:
        self._thickness = max(12, int(thickness_px))
        self._horizontal.set_thickness(self._thickness)
        self._vertical.set_thickness(self._thickness)
        self._corner.setFixedSize(self._thickness, self._thickness)
        self.updateGeometry()

    def _attach_cursor_tracking(self) -> None:
        input_handler = getattr(self._canvas, "input_handler", None)
        if input_handler is None:
            return
        hover = getattr(input_handler, "pointer_hover", None)
        if hover is None:
            return

        def _on_hover(point: object) -> None:
            pos = getattr(point, "pos", None)
            if not isinstance(pos, tuple) or len(pos) < 2:
                self._horizontal.set_cursor_canvas_pos(None)
                self._vertical.set_cursor_canvas_pos(None)
                return
            canvas_pos = QPointF(float(pos[0]), float(pos[1]))
            self._horizontal.set_cursor_canvas_pos(canvas_pos)
            self._vertical.set_cursor_canvas_pos(canvas_pos)

        hover.connect(_on_hover)
