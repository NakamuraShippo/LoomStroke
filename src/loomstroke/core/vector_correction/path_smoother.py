"""Laplacian smoothing of anchor positions; open-path endpoints never move."""

from __future__ import annotations

from collections.abc import Sequence

from .path_correction_result import PathCorrectionResult

__all__ = ["PathSmoother"]

Point = tuple[float, float]


class PathSmoother:
    def smooth(
        self,
        points: Sequence[Point],
        *,
        strength: float = 0.5,
        iterations: int = 1,
        closed: bool = False,
    ) -> PathCorrectionResult:
        count = len(points)
        strength = max(0.0, min(1.0, float(strength)))
        if count < 3 or strength <= 0.0 or iterations < 1:
            return PathCorrectionResult(tuple(points), tuple(range(count)))
        current = [tuple(map(float, p)) for p in points]
        for _ in range(min(int(iterations), 32)):
            updated = list(current)
            indexes = range(count) if closed else range(1, count - 1)
            for i in indexes:
                prev_p = current[(i - 1) % count]
                next_p = current[(i + 1) % count]
                mx, my = (prev_p[0] + next_p[0]) / 2.0, (prev_p[1] + next_p[1]) / 2.0
                x, y = current[i]
                updated[i] = (x + (mx - x) * strength, y + (my - y) * strength)
            current = updated
        return PathCorrectionResult(tuple(current), tuple(range(count)))
