"""Page-scoped assist state + stroke sessions + overlay painting (CT-14 UI integration).

State lives in the page view state (``manga_drawing_assists``) and changes go
through ``SetPageViewStateKeyCommand``; the overlay is drawn by the canvas
widget in canvas coordinates (cosmetic pens, never exported).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from loomstroke.core.commands_page_view_state_key import SetPageViewStateKeyCommand
from loomstroke.core.drawing_assist import (
    DrawingAssist,
    DrawingAssistState,
    DrawingAssistValidator,
    DrawingAssistViewStateCodec,
    OverlayGeometryBuilder,
    StrokeConstraintSession,
)
from loomstroke.core.drawing_assist.assist_overlay_geometry import AssistOverlayGeometry
from loomstroke.core.drawing_assist.position_snap import snap_to_geometry
from loomstroke.core.drawing_assist.stroke_feedback import stroke_feedback

from .perspective_handle_interaction import PerspectiveHandleInteraction

__all__ = ["DrawingAssistController"]


class DrawingAssistController:
    def __init__(self, host: Any, *, repaint: Callable[[], None] | None = None) -> None:
        self._host = host
        self._repaint = repaint or (lambda: None)
        self._codec = DrawingAssistViewStateCodec()
        self._validator = DrawingAssistValidator()
        self._overlay = OverlayGeometryBuilder()
        self.overlay_visible = True
        self.overlay_color = QColor(0, 150, 255)
        self.overlay_opacity = 0.6
        self.handles = PerspectiveHandleInteraction(self)
        self._stroke_session: StrokeConstraintSession | None = None
        self._stroke_page = ""
        self._stroke_feedback = AssistOverlayGeometry()
        self._feedback_is_position = False
        self._snap_cache = None

    # -- state -------------------------------------------------------------------------

    def page_id(self) -> str:
        pages = getattr(self._host, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        try:
            value = getter() if callable(getter) else ""
        except Exception:
            value = ""
        return str(value or "")

    def canvas_size(self) -> tuple[float, float]:
        canvas = getattr(self._host, "canvas", None)
        width = float(getattr(canvas, "_width", 0) or 0)
        height = float(getattr(canvas, "_height", 0) or 0)
        return (width or 1.0, height or 1.0)

    def state(self) -> DrawingAssistState:
        pages = getattr(self._host, "pages", None)
        getter = getattr(pages, "get_page_view_state", None)
        if not callable(getter):
            return DrawingAssistState()
        try:
            return self._codec.read(getter(self.page_id()))
        except Exception:
            return DrawingAssistState()

    def set_state(self, state: DrawingAssistState, *, label: str = "Drawing Assist") -> bool:
        canvas = getattr(self._host, "canvas", None)
        pages = getattr(self._host, "pages", None)
        if canvas is None or pages is None:
            return False
        command = SetPageViewStateKeyCommand(
            pages, self.page_id(), self._codec.KEY, self._codec.payload(state)
        )
        begin = getattr(canvas, "begin_transaction", None)
        if callable(begin):
            begin(label)
        try:
            ok = bool(canvas.submit(command))
        finally:
            end = getattr(canvas, "end_transaction", None)
            if callable(end):
                end()
        self._repaint()
        return ok

    def active_assist(self) -> DrawingAssist | None:
        return self.state().active()

    def issues(self, assist: DrawingAssist) -> tuple[Any, ...]:
        return self._validator.validate(assist, self.canvas_size())

    # -- edits -------------------------------------------------------------------------

    def add(self, assist_type: str, *, activate: bool = True) -> DrawingAssist | None:
        assist = self.default_assist(assist_type)
        if assist is None:
            return None
        ok = self.set_state(self.state().with_item(assist, activate=activate), label="Add Assist")
        return assist if ok else None

    def update(self, assist: DrawingAssist) -> bool:
        return self.set_state(self.state().with_item(assist), label="Edit Assist")

    def remove(self, assist_id: str) -> bool:
        return self.set_state(self.state().without(assist_id), label="Remove Assist")

    def set_active(self, assist_id: str | None) -> bool:
        return self.set_state(self.state().with_active(assist_id), label="Activate Assist")

    def rotate_active_straight_ruler(self, degrees: float) -> bool:
        assist = self.active_assist()
        canvas = getattr(self._host, "canvas", None)
        if (
            assist is None
            or assist.type != "parallel"
            or assist.locked
            or getattr(canvas, "_in_transaction", False)
            or self.issues(assist)
        ):
            return False
        angle = (assist.number("angle", 0.0) + degrees + 180.0) % 360.0 - 180.0
        return self.update(assist.with_geometry(angle=angle))

    def default_assist(self, assist_type: str) -> DrawingAssist | None:
        width, height = self.canvas_size()
        cx, cy = width / 2.0, height / 2.0
        assist_id = f"assist-{uuid.uuid4().hex[:8]}"
        defaults: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
            "mirror": ({"p0": [cx, 0.0], "p1": [cx, height]}, {"include_original": True}),
            "rotational": ({"center": [cx, cy]}, {"count": 6, "include_original": True}),
            "parallel": ({"angle": 0.0}, {}),
            "radial": ({"center": [cx, cy]}, {}),
            "concentric": ({"center": [cx, cy]}, {"ratio": 1.0}),
            "perspective": ({"vanishing_points": [[cx, cy]]}, {"axis": "auto"}),
        }
        if assist_type not in defaults:
            return None
        geometry, options = defaults[assist_type]
        return DrawingAssist(assist_id, assist_type, geometry=geometry, options=options)

    # -- strokes -----------------------------------------------------------------------

    def begin_session(self, tool_id: str) -> StrokeConstraintSession | None:
        self.clear_stroke_feedback()
        assist = self.active_assist()
        if assist is None or self.issues(assist):
            return None
        self._stroke_session = StrokeConstraintSession.compile(assist)
        self._stroke_page = self.page_id()
        return self._stroke_session

    def set_stroke_feedback(self, session: StrokeConstraintSession, point: Any) -> None:
        if session is not self._stroke_session:
            return
        if point is None or self.page_id() != self._stroke_page:
            self.clear_stroke_feedback()
            return
        geometry = stroke_feedback(session, point, self.canvas_size())
        self._feedback_is_position = False
        self._stroke_feedback = geometry

    def clear_stroke_feedback(self) -> None:
        self._stroke_session = None
        self._stroke_feedback = AssistOverlayGeometry()
        self._feedback_is_position = False

    def snap_position(self, x, y, w=0.0, h=0.0):
        assist = self.active_assist() if self.overlay_visible else None
        if assist is None or self.issues(assist):
            return x, y, AssistOverlayGeometry()
        size = self.canvas_size()
        key = (assist, size)
        if self._snap_cache is None or self._snap_cache[0] != key:
            self._snap_cache = (key, self._overlay.build(assist, size))
        ui = getattr(self._host, "ui", None)
        window = getattr(ui, "get_main_window", lambda: None)()
        viewport = getattr(getattr(window, "canvas", None), "viewport", None)
        radius = getattr(viewport, "screen_to_canvas_distance", lambda value: value)(8)
        best = None
        for px, py in dict.fromkeys(((x, y), (x + w, y), (x, y + h), (x + w, y + h))):
            point, geometry = snap_to_geometry(self._snap_cache[1], (px, py), radius)
            if not geometry.is_empty:
                distance = (point[0] - px) ** 2 + (point[1] - py) ** 2
                if best is None or distance < best[0]:
                    best = (distance, x + point[0] - px, y + point[1] - py, geometry)
        return (x, y, AssistOverlayGeometry()) if best is None else best[1:]

    def set_position_feedback(self, geometry):
        if geometry.is_empty and not self._feedback_is_position:
            return
        if geometry != self._stroke_feedback:
            self._stroke_feedback = geometry
            self._stroke_page = self.page_id()
            self._feedback_is_position = True

    def clear_position_feedback(self):
        if self._feedback_is_position:
            self.clear_stroke_feedback()

    # -- overlay -----------------------------------------------------------------------

    def draw(self, painter: QPainter) -> None:
        if not self.overlay_visible:
            return
        state = self.state()
        if state.is_empty:
            return
        size = self.canvas_size()
        painter.save()
        try:
            for stored in state.items:
                preview = self.handles.preview
                assist = preview if preview is not None and stored.id == preview.id else stored
                if not assist.enabled or not assist.is_known:
                    continue
                geometry = self._overlay.build(assist, size)
                pen = self._pen(active=assist.id == state.active_id)
                painter.setPen(pen)
                for (x0, y0), (x1, y1) in geometry.lines:
                    painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
                for cx, cy, rx, ry in geometry.ellipses:
                    painter.drawEllipse(QRectF(cx - rx, cy - ry, 2.0 * rx, 2.0 * ry))
                for x, y, label in geometry.markers:
                    painter.drawEllipse(QPointF(x, y), 3.0, 3.0)
                    painter.drawText(QPointF(x + 4.0, y - 4.0), label)
        finally:
            painter.restore()

    def _pen(self, *, active: bool) -> QPen:
        color = QColor(self.overlay_color)
        color.setAlphaF(max(0.0, min(1.0, self.overlay_opacity)))
        pen = QPen(color, 1.5 if active else 1.0)
        pen.setCosmetic(True)
        if not active:
            pen.setStyle(Qt.PenStyle.DashLine)
        return pen
