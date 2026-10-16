"""Whole-stroke helpers over ``StrokeConstraintSession`` (the spec's ``constrain_stroke`` /
``expand_stroke``): used by tests, previews and tools that batch their points."""

from __future__ import annotations

from collections.abc import Sequence

from .assisted_stroke import AssistedStroke
from .drawing_assist import DrawingAssist
from .stroke_constraint_session import StrokeConstraintSession

__all__ = ["StrokeExpander"]

Point = tuple[float, float]


class StrokeExpander:
    def constrain(self, points: Sequence[Point], assist: DrawingAssist | None) -> tuple[Point, ...]:
        session = StrokeConstraintSession.compile(assist)
        if session is None:
            return tuple((float(x), float(y)) for x, y in points)
        return tuple(session.constrain(float(x), float(y)) for x, y in points)

    def expand(
        self, points: Sequence[Point], assist: DrawingAssist | None
    ) -> tuple[AssistedStroke, ...]:
        session = StrokeConstraintSession.compile(assist)
        constrained = self.constrain(points, assist)
        if session is None or not session.has_copies:
            return (AssistedStroke("master", 0, constrained),)
        strokes: list[AssistedStroke] = []
        if session.include_original:
            strokes.append(AssistedStroke("master", 0, constrained))
        copies: list[list[Point]] = [[] for _ in range(session.copy_count)]
        for x, y in constrained:
            for index, copy_point in enumerate(session.copies(x, y)):
                copies[index].append(copy_point)
        for index, copy_points in enumerate(copies, start=1):
            strokes.append(AssistedStroke("copy", index, tuple(copy_points)))
        return tuple(strokes)
