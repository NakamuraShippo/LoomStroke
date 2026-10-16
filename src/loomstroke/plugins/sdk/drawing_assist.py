"""Shared drawing-assist adapter for stroke tools (CT-14 ``plugins/sdk/drawing_assist.py``).

Usage in a tool::

    from ..sdk.drawing_assist import ToolAssistBridge

    self._assist = ToolAssistBridge("basic_pen")
    pt = self._assist.begin(self.host_api, pt)        # pointer down
    pts = self._assist.points(pts)                    # pointer move
    pt = self._assist.point(pt)                       # pointer up
    for command in self._assist.commands(points, build):  # build(points) -> Command | None
        ...
    self._assist.end()

The bridge asks the canvas widget's ``DrawingAssistController`` for a compiled
``StrokeConstraintSession`` at stroke start and keeps it for the whole stroke so
preview and commit use the same snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from typing import Any

from loomstroke.core.drawing_assist import StrokeConstraintSession

__all__ = ["SUPPORTED_TOOL_IDS", "ToolAssistBridge", "get_drawing_assist_controller"]

SUPPORTED_TOOL_IDS: frozenset[str] = frozenset({"basic_pen", "basic_eraser", "effect_pen"})


def get_drawing_assist_controller(host_api: Any) -> Any:
    """Return the ``DrawingAssistController`` attached to the canvas widget, or ``None``."""
    if host_api is None:
        return None
    ui = getattr(host_api, "ui", None)
    if ui is None:
        return None
    main_window = getattr(ui, "get_main_window", lambda: None)()
    if main_window is None:
        return None
    canvas_widget = getattr(main_window, "canvas", None)
    return getattr(canvas_widget, "_drawing_assist_controller", None)


class ToolAssistBridge:
    def __init__(self, tool_id: str) -> None:
        self._tool_id = tool_id
        self._session: StrokeConstraintSession | None = None

    @property
    def session(self) -> StrokeConstraintSession | None:
        return self._session

    @property
    def active(self) -> bool:
        return self._session is not None

    def begin(self, host_api: Any, point: Any) -> Any:
        """Start a stroke: compile the active assist (if any) and constrain the first point."""
        self._session = None
        if self._tool_id in SUPPORTED_TOOL_IDS:
            controller = get_drawing_assist_controller(host_api)
            begin_session = getattr(controller, "begin_session", None)
            if callable(begin_session):
                self._session = begin_session(self._tool_id)
        return self.point(point)

    def end(self) -> None:
        self._session = None

    def point(self, point: Any) -> Any:
        if self._session is None or (
            self._session.constraint is None and self._session.anchor is not None
        ):
            if self._session is not None and self._session.anchor is None:
                self._session.constrain(*self._pos(point))
            return point
        x, y = self._session.constrain(*self._pos(point))
        return self._with_pos(point, (x, y))

    def points(self, points: Iterable[Any]) -> list[Any]:
        return [self.point(point) for point in points]

    def commands(
        self, points: Sequence[Any], build: Callable[[list[Any]], Any | None]
    ) -> list[Any]:
        """Master command plus one command per symmetry copy (same merge key via *build*)."""
        commands: list[Any] = []
        session = self._session
        if session is None or not session.has_copies:
            command = build(list(points))
            return [command] if command is not None else []
        if session.include_original:
            command = build(list(points))
            if command is not None:
                commands.append(command)
        copies: list[list[Any]] = [[] for _ in range(session.copy_count)]
        for point in points:
            for index, (x, y) in enumerate(session.copies(*self._pos(point))):
                if index < len(copies):
                    copies[index].append(self._with_pos(point, (x, y)))
        for copy_points in copies:
            command = build(copy_points)
            if command is not None:
                commands.append(command)
        return commands

    # -- point helpers -----------------------------------------------------------------

    @staticmethod
    def _pos(point: Any) -> tuple[float, float]:
        pos = getattr(point, "pos", None)
        if pos is None:
            return (float(point[0]), float(point[1]))
        return (float(pos[0]), float(pos[1]))

    @staticmethod
    def _with_pos(point: Any, pos: tuple[float, float]) -> Any:
        try:
            return replace(point, pos=pos)
        except TypeError:
            clone = type(point).__new__(type(point))
            clone.__dict__.update(getattr(point, "__dict__", {}))
            clone.pos = pos
            return clone
