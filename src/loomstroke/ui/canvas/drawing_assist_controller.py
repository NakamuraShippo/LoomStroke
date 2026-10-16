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
        self.set_state(self.state().with_item(assist, activate=activate), label="Add Assist")
        return assist

    def update(self, assist: DrawingAssist) -> bool:
        return self.set_state(self.state().with_item(assist), label="Edit Assist")

    def remove(self, assist_id: str) -> bool:
        return self.set_state(self.state().without(assist_id), label="Remove Assist")

    def set_active(self, assist_id: str | None) -> bool:
        return self.set_state(self.state().with_active(assist_id), label="Activate Assist")

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
        assist = self.active_assist()
        if assist is None or self.issues(assist):
            return None
        return StrokeConstraintSession.compile(assist)

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
            for assist in state.items:
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
