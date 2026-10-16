"""Ramer-Douglas-Peucker simplification keeping endpoints and protected anchors."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from itertools import pairwise

from .path_correction_result import PathCorrectionResult

__all__ = ["PathSimplifier"]

Point = tuple[float, float]


class PathSimplifier:
    def simplify(
        self,
        points: Sequence[Point],
        tolerance: float,
        *,
        protected: Iterable[int] = (),
        closed: bool = False,
    ) -> PathCorrectionResult:
        count = len(points)
        if count < 3 or tolerance <= 0 or not math.isfinite(tolerance):
            return PathCorrectionResult(tuple(points), tuple(range(count)))
        keep = {0, count - 1}
        keep.update(i for i in protected if 0 <= i < count)
        if closed:
            # Keep the farthest point from the first as a second pivot.
            farthest = max(range(count), key=lambda i: math.dist(points[0], points[i]))
            keep.add(farthest)
        anchors = sorted(keep)
        for a, b in pairwise(anchors):
            self._rdp(points, a, b, tolerance, keep)
        kept = tuple(sorted(keep))
        return PathCorrectionResult(tuple(points[i] for i in kept), kept)

    def _rdp(
        self, points: Sequence[Point], start: int, end: int, tol: float, keep: set[int]
    ) -> None:
        stack = [(start, end)]
        while stack:
            a, b = stack.pop()
            if b - a < 2:
                continue
            farthest, distance = a, -1.0
            for i in range(a + 1, b):
                d = self._distance(points[i], points[a], points[b])
                if d > distance:
                    farthest, distance = i, d
            if distance > tol:
                keep.add(farthest)
                stack.append((a, farthest))
                stack.append((farthest, b))

    @staticmethod
    def _distance(p: Point, a: Point, b: Point) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length_sq = dx * dx + dy * dy
        if length_sq < 1e-18:
            return math.dist(p, a)
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq))
        return math.dist(p, (a[0] + t * dx, a[1] + t * dy))
