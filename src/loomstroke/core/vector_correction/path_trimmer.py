"""Trim or extend one end of a polyline to its intersection with a target polyline."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from .path_correction_result import PathCorrectionResult
from .segment_intersector import SegmentIntersector

__all__ = ["PathTrimmer"]

Point = tuple[float, float]
End = Literal["start", "end"]


class PathTrimmer:
    REACH_FACTOR = 50.0

    def trim(
        self, points: Sequence[Point], target: Sequence[Point], *, end: End = "end"
    ) -> PathCorrectionResult:
        """Cut the polyline where it crosses *target* (nearest crossing to the chosen end)."""
        count = len(points)
        if count < 2 or len(target) < 2:
            return PathCorrectionResult(
                tuple(points), tuple(range(count)), ("trim_target_missing",)
            )
        ordered = list(points) if end == "end" else list(reversed(points))
        for index in range(len(ordered) - 2, -1, -1):
            hits = SegmentIntersector.with_polyline(ordered[index], ordered[index + 1], target)
            if not hits:
                continue
            hit = hits[-1]  # crossing closest to the trimmed end
            trimmed = [*ordered[: index + 1], hit.point]
            if end == "start":
                trimmed.reverse()
            kept = (
                tuple(range(index + 1)) if end == "end" else tuple(range(count - index - 1, count))
            )
            return PathCorrectionResult(tuple(trimmed), kept)
        return PathCorrectionResult(tuple(points), tuple(range(count)), ("trim_no_intersection",))

    def extend(
        self, points: Sequence[Point], target: Sequence[Point], *, end: End = "end"
    ) -> PathCorrectionResult:
        """Extend the end segment along its direction until it meets *target*."""
        count = len(points)
        if count < 2 or len(target) < 2:
            return PathCorrectionResult(
                tuple(points), tuple(range(count)), ("trim_target_missing",)
            )
        tip, inner = (points[-1], points[-2]) if end == "end" else (points[0], points[1])
        dx, dy = tip[0] - inner[0], tip[1] - inner[1]
        length = math.hypot(dx, dy)
        if length < 1e-12:
            return PathCorrectionResult(tuple(points), tuple(range(count)), ("path_too_short",))
        reach = max(length, self._extent(points, target)) * self.REACH_FACTOR
        far = (tip[0] + dx / length * reach, tip[1] + dy / length * reach)
        hits = SegmentIntersector.with_polyline(tip, far, target)
        if not hits:
            return PathCorrectionResult(
                tuple(points), tuple(range(count)), ("extend_no_intersection",)
            )
        new_tip = hits[0].point
        if end == "end":
            return PathCorrectionResult((*tuple(points[:-1]), new_tip), tuple(range(count - 1)))
        return PathCorrectionResult((new_tip, *tuple(points[1:])), tuple(range(1, count)))

    @staticmethod
    def _extent(a: Sequence[Point], b: Sequence[Point]) -> float:
        xs = [p[0] for p in a] + [p[0] for p in b]
        ys = [p[1] for p in a] + [p[1] for p in b]
        return max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
