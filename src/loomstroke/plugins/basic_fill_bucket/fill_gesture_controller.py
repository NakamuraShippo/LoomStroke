"""Capture enclosing and gap-fill gestures without changing document pixels."""

from __future__ import annotations

import math
from typing import Any

from loomstroke.core.fill.fill_gesture_mask import FillGestureMask

from ..basic_selection.free_selection_overlay import FreeSelectionOverlay
from ..sdk.assist_geometry import mapped_paths, path_copies, snap_assist_point


class FillGestureController:
    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self._points: list[tuple[float, float]] = []
        self._mode = ""
        self._size = 32.0
        self._overlay = FreeSelectionOverlay()

    def down(self, point: Any) -> None:
        self.cancel()
        point = snap_assist_point(self._tool.host_api, point)
        position = tuple(map(float, point.pos))
        if not all(math.isfinite(value) for value in position):
            return
        self._points = [position]
        self._mode = self._tool.settings.fill_mode
        self._size = self._tool.settings.gap_fill_size
        self._tool.host_api.input.pointer_capture(self._tool.descriptor().id)
        self._preview()

    def move(self, points: list[Any]) -> None:
        if not self._points:
            return
        for source_point in points:
            point = snap_assist_point(self._tool.host_api, source_point)
            position = tuple(map(float, point.pos))
            if (
                all(math.isfinite(v) for v in position)
                and math.dist(position, self._points[-1]) >= 0.5
            ):
                if len(self._points) >= 8192:
                    self._points = self._points[::2]
                self._points.append(position)
        self._preview()

    def up(self, point: Any) -> None:
        if not self._points:
            return
        self.move([point])
        size = self._tool.host_api.canvas.get_canvas_size()
        session = self._tool._assist.session
        enclosure = (
            mapped_paths(self._path(copies=False), session)
            if session is not None and session.has_copies
            else FillGestureMask.render_path(self._path(), size)
        )
        self.cancel()
        if enclosure is not None:
            self._tool._preparation.prepare(point, enclosure)

    def cancel(self) -> None:
        if self._points and self._tool.host_api is not None:
            self._tool.host_api.input.release_capture(self._tool.descriptor().id)
        self._points = []
        self._overlay.clear()

    def _preview(self) -> None:
        self._overlay.show(self._tool.host_api, self._path(preview=True))

    def _path(self, *, preview=False, copies=True):
        path = FillGestureMask.path(self._mode, self._points, self._size)
        session = getattr(getattr(self._tool, "_assist", None), "session", None)
        return path_copies(path, session, union=not preview) if copies else path
