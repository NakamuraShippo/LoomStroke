"""Nearest point on a bounded set of composition segments, without Qt."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .composition_geometry import Line


def nearest_line_hit(
    point: tuple[float, float], lines: Iterable[Line], threshold: float
) -> tuple[tuple[float, float], int] | None:
    if threshold < 0 or not all(math.isfinite(v) for v in (*point, threshold)):
        return None
    x, y = point
    best = None
    distance = threshold * threshold
    for index, ((ax, ay), (bx, by)) in enumerate(lines):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length)) if length else 0
        candidate = ax + t * dx, ay + t * dy
        squared = (candidate[0] - x) ** 2 + (candidate[1] - y) ** 2
        if squared <= distance and (best is None or squared < distance):
            distance, best = squared, (candidate, index)
    return best


def nearest_line_point(
    point: tuple[float, float], lines: Iterable[Line], threshold: float
) -> tuple[float, float] | None:
    hit = nearest_line_hit(point, lines, threshold)
    return hit[0] if hit is not None else None
