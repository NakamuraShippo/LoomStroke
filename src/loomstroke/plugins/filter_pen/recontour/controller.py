"""Filter Pen curve editing; the service recomputes pixels from the stroke source."""

from __future__ import annotations

import math
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QTimer

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.core.recontour.geometry import fit, move_anchor

from .overlay import draw

_FITTER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="filter-pen-fit")


def _fit(lanes: dict, smoothness: float) -> dict:
    return {
        lane: fit(tuple((point.x, point.y) for point in points), smoothness)
        for lane, points in lanes.items()
        if points
    }


class FilterPenRecontourController:
    def __init__(self, tool: Any) -> None:
        self.tool = tool
        self.lanes: dict = {}
        self.curves: dict = {}
        self.editing: dict = {}
        self.selected: tuple | None = None
        self.drag: tuple | None = None
        self.start = (0.0, 0.0)
        self.before_drag: dict = {}
        self.widget: Any = None
        self.future: Future | None = None
        self._epoch = 0
        self._timer = QTimer(tool)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._poll)

    @property
    def has_path(self) -> bool:
        return bool(self.lanes)

    def _valid(self) -> bool:
        return bool(self.tool._service is not None and self.tool._service.editable)

    def close(self) -> None:
        self._epoch += 1
        self._timer.stop()
        if self.future is not None:
            self.future.cancel()
            self.future = None
        self.lanes = {}
        self.curves = {}
        self.editing = {}
        self.drag = None
        self.selected = None
        if self.widget is not None:
            self.widget.set_tool_overlay_fn(None)
            self.widget = None

    def prepare(self, lanes: dict, smoothness: float) -> None:
        self.close()
        if not self._valid():
            return
        self.lanes = {lane: tuple(points) for lane, points in lanes.items() if points}
        if not self.lanes:
            return
        self.future = _FITTER.submit(_fit, dict(self.lanes), smoothness)
        self._timer.setInterval(16)
        self._timer.start()

    def _poll(self) -> None:
        if not self._valid():
            self.close()
            return
        if self.future is None or not self.future.done():
            return
        try:
            self.curves = self.future.result()
        except Exception:
            self.tool._failed(
                "Recontour could not edit this stroke. The raster stroke was preserved."
            )
            self.close()
            return
        self.future = None
        self.editing = {lane: curve.anchors for lane, curve in self.curves.items()}
        ui = getattr(self.tool.host_api, "ui", None)
        window = getattr(ui, "get_main_window", lambda: None)()
        widget = getattr(window, "canvas", None)
        if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
            self.widget = widget
            widget.set_tool_overlay_fn(self.paint)
        # Keep checking the public session validity after Undo or another Command.
        self._timer.setInterval(100)

    def paint(self, painter: Any) -> None:
        if self._valid():
            draw(painter, self.editing, self.selected)

    def _scale(self) -> float:
        viewport = getattr(self.widget, "viewport", None)
        convert = getattr(viewport, "canvas_to_widget", None)
        if callable(convert):
            a, b = convert(0, 0), convert(1, 0)
            return max(0.01, math.dist(a, b))
        return 1.0

    def press(self, point: Any) -> bool:
        if not self.curves or not self._valid():
            return False
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
        if not self._valid():
            self.close()
            return True
        if points:
            lane, index, part = self.drag
            anchors = self.before_drag[lane]
            x, y = getattr(anchors[index], part)
            point = points[-1]
            pos = (x + point.pos[0] - self.start[0], y + point.pos[1] - self.start[1])
            self.editing[lane] = move_anchor(anchors, index, part, pos)
            if self.widget is not None:
                self.widget.set_tool_overlay_fn(self.paint)
        return True

    def _replace(self, edited: dict) -> bool:
        if not self._valid():
            return False
        points = []
        for lane, curve in self.curves.items():
            positions = curve.project(edited[lane])
            points.extend(
                replace(point, x=xy[0], y=xy[1])
                for point, xy in zip(self.lanes[lane], positions, strict=True)
            )
        return bool(self.tool._service.replace_path(points))

    def release(self, point: Any) -> bool:
        if self.drag is None:
            return False
        self.move([point])
        self.drag = None
        if not self._replace(self.editing):
            self.close()
        return True

    def key(self, key: str, pressed: bool) -> bool:
        if not pressed or not self.curves:
            return False
        if key.lower() in ("enter", "return"):
            self.close()
            self.tool._service.finalize()
            return True
        if key.lower() in ("esc", "escape"):
            if self.drag is not None:
                self.editing = dict(self.before_drag)
                self.drag = None
            else:
                self._replace({lane: curve.anchors for lane, curve in self.curves.items()})
                self.close()
            return True
        return False


# Compatibility aliases for existing imports.
EffectPenRecontourController = FilterPenRecontourController


_bind_legacy_names(__name__, {"EffectPenRecontourController": "FilterPenRecontourController"})
