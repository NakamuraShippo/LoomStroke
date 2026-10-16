"""Pen-only lifetime and input routing; pixels remain owned by core commands."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QTimer

from ....core.recontour.commands import RecontourCommand
from ....core.recontour.geometry import move_anchor
from ....core.recontour.session import RasterStrokeSession
from ....ui.translations import translated_literal_domain
from .overlay import draw


class RecontourController:
    def __init__(self, tool: Any) -> None:
        self.tool = tool
        self.record: RasterStrokeSession | None = None
        self.drag: tuple | None = None
        self.selected: tuple | None = None
        self.editing: dict = {}
        self.start: tuple[float, float] = (0, 0)
        self.before_drag: dict = {}
        self.widget: Any = None
        self._bus: Any = None
        self._subscriptions: list[tuple[str, Any, Any]] = []

    def bind(self) -> None:
        self.unbind()
        self._bus = getattr(self.tool.host_api, "events", None)
        subscribe = getattr(self._bus, "subscribe", None)
        if callable(subscribe):
            for topic in (
                "pages.active.changing",
                "layer.selected",
                "doc.created",
                "doc.loaded",
                "doc.opened",
                "doc.closed",
                "doc.saved",
            ):
                handler = self._layer_selected if topic == "layer.selected" else self._boundary
                token = subscribe(topic, handler)
                self._subscriptions.append((topic, token, handler))
            for topic in ("history.changed", "doc.changed", "selection.changed"):
                handler = self._check_boundary
                token = subscribe(topic, handler)
                self._subscriptions.append((topic, token, handler))

    def unbind(self) -> None:
        unsubscribe = getattr(self._bus, "off", None)
        if callable(unsubscribe):
            for topic, token, _handler in self._subscriptions:
                unsubscribe(topic, token)
        self._subscriptions.clear()
        self._bus = None

    def _boundary(self, *args: Any, **kwargs: Any) -> None:
        self.finish()

    def _layer_selected(self, payload: Any) -> None:
        record = self.record
        layer_id = (
            payload.get("layer_id")
            if isinstance(payload, dict)
            else getattr(payload, "layer_id", None)
        )
        if record is not None and record.layer is not None and layer_id == record.layer.id:
            # Content refresh reselects the current panel row after an adjustment.
            # Only a real target/state change invalidates the editable stroke.
            self._check_boundary()
            return
        self.finish()

    def _check_boundary(self, *args: Any, **kwargs: Any) -> None:
        if self.record is not None and self.record.ready:
            record = self.record
            QTimer.singleShot(0, lambda: self._refresh_record(record))

    def _refresh_record(self, record: RasterStrokeSession) -> None:
        if self.record is record:
            self.refresh()

    def canvas(self) -> Any:
        return getattr(self.tool.host_api, "canvas", None)

    def finish(self) -> None:
        if self.record is not None:
            self.record.close()
        self.record = None
        self.drag = None
        self.selected = None
        self.editing = {}
        if self.widget is not None:
            self.widget.set_tool_overlay_fn(None)
            self.widget = None

    def begin(self) -> None:
        self.finish()
        if self.tool.settings.recontour:
            self.record = RasterStrokeSession(self.tool.settings.recontour_smoothness)

    def wrap(self, commands: list) -> list:
        if self.record is None:
            return commands
        return [
            RecontourCommand(self.record, "capture", stroke=cmd, lane=index)
            for index, cmd in enumerate(commands)
        ]

    def seal(self) -> None:
        if self.record is not None:
            self.tool._pending_commands.append(RecontourCommand(self.record, "seal"))
            record = self.record
            QTimer.singleShot(0, lambda: self._refresh_record(record))

    def refresh(self) -> None:
        record = self.record
        if record is None:
            return
        record.poll(self.canvas())
        if record.error:
            message = translated_literal_domain("BasicPen", record.error, use_fallback=True)
            notify = getattr(getattr(self.tool.host_api, "ui", None), "show_notification", None)
            if callable(notify):
                notify(message, level="warning")
            self.finish()
            return
        if not record.valid(self.canvas()):
            self.finish()
            return
        if not record.ready:
            if record.future is not None:
                QTimer.singleShot(16, lambda: self._refresh_record(record))
            return
        if not self.tool.settings.recontour:
            self.finish()
            return
        if self.drag is None:
            self.editing = dict(record.edited)
        ui = getattr(self.tool.host_api, "ui", None)
        window = getattr(ui, "get_main_window", lambda: None)()
        widget = getattr(window, "canvas", None)
        if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
            self.widget = widget
            widget.set_tool_overlay_fn(self.paint)

    def paint(self, painter: Any) -> None:
        if self.record is None or not self.record.valid(self.canvas()):
            return
        draw(painter, self.editing, self.selected)

    def _scale(self) -> float:
        viewport = getattr(self.widget, "viewport", None)
        convert = getattr(viewport, "canvas_to_widget", None)
        if callable(convert):
            a, b = convert(0, 0), convert(1, 0)
            return max(0.01, math.hypot(b[0] - a[0], b[1] - a[1]))
        return 1.0

    def press(self, point: Any) -> bool:
        record = self.record
        if record is None or not record.ready or not record.valid(self.canvas()):
            return False
        self.editing = dict(record.edited)
        limit = (8 / self._scale()) ** 2
        hits = []
        for lane, anchors in self.editing.items():
            for index, anchor in enumerate(anchors):
                parts = ["pos"]
                if self.selected is not None and self.selected[:2] == (lane, index):
                    if index > 0:
                        parts.append("incoming")
                    if index + 1 < len(anchors):
                        parts.append("outgoing")
                for part in parts:
                    x, y = getattr(anchor, part)
                    distance = (point.pos[0] - x) ** 2 + (point.pos[1] - y) ** 2
                    if distance <= limit:
                        hits.append((distance, lane, index, part))
        if not hits:
            return False
        _, lane, index, part = min(hits)
        self.drag = (lane, index, part)
        self.selected = self.drag
        self.start = point.pos
        self.before_drag = dict(self.editing)
        return True

    def move(self, points: list) -> bool:
        if self.drag is None:
            return False
        if not points:
            return True
        lane, index, part = self.drag
        anchors = self.before_drag[lane]
        x, y = getattr(anchors[index], part)
        point = points[-1]
        pos = (x + point.pos[0] - self.start[0], y + point.pos[1] - self.start[1])
        self.editing[lane] = move_anchor(anchors, index, part, pos)
        if self.widget is not None:
            self.widget.set_tool_overlay_fn(self.paint)
        return True

    def release(self, point: Any) -> bool:
        if self.drag is None:
            return False
        self.move([point])
        if self.record is not None:
            self.tool._pending_commands.append(
                RecontourCommand(self.record, "commit", edited=dict(self.editing))
            )
        self.drag = None
        record = self.record
        QTimer.singleShot(0, lambda: self._refresh_record(record))
        return True

    def key(self, key: str, pressed: bool) -> bool:
        if not pressed or self.record is None or not self.record.ready:
            return False
        if key.lower() in ("enter", "return"):
            self.finish()
            return True
        if key.lower() in ("esc", "escape"):
            if self.drag is not None:
                self.editing = dict(self.before_drag)
                self.drag = None
                self.refresh()
            else:
                record = self.record
                base = {lane: curve.anchors for lane, curve in record.curves.items()}
                canvas = self.canvas()
                if canvas is not None:
                    canvas.submit(RecontourCommand(record, "cancel", edited=base))
                self.finish()
            return True
        return False
