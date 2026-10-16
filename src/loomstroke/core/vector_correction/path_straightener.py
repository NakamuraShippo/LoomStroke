"""Pull anchors toward the chord between the path endpoints (``amount`` 1.0 = straight line)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .path_correction_result import PathCorrectionResult

__all__ = ["PathStraightener"]

Point = tuple[float, float]


class PathStraightener:
    def straighten(self, points: Sequence[Point], *, amount: float = 1.0) -> PathCorrectionResult:
        count = len(points)
        amount = max(0.0, min(1.0, float(amount)))
        if count < 3 or amount <= 0.0:
            return PathCorrectionResult(tuple(points), tuple(range(count)))
        a, b = points[0], points[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length_sq = dx * dx + dy * dy
        if length_sq < 1e-18:
            return PathCorrectionResult(
                tuple(points), tuple(range(count)), ("path_chord_degenerate",)
            )
        out: list[Point] = [tuple(map(float, a))]
        for p in points[1:-1]:
            t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_sq
            px, py = a[0] + t * dx, a[1] + t * dy
            out.append((p[0] + (px - p[0]) * amount, p[1] + (py - p[1]) * amount))
        out.append(tuple(map(float, b)))
        if not all(math.isfinite(x) and math.isfinite(y) for x, y in out):
            return PathCorrectionResult(tuple(points), tuple(range(count)), ("path_not_finite",))
        return PathCorrectionResult(tuple(out), tuple(range(count)))
