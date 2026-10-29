"""Qt-free rotation, projection and clipping of guide geometry."""

from __future__ import annotations

import math
from functools import lru_cache

from .composition_geometry import Line


def rotate_point(point: tuple, pivot: tuple, angle: float) -> tuple[float, float]:
    if not all(math.isfinite(float(v)) for v in (*point, *pivot, angle)):
        raise ValueError("Non-finite guide transform")
    if angle % 360 == 0:
        return float(point[0]), float(point[1])
    x, y = point[0] - pivot[0], point[1] - pivot[1]
    cardinal = {90: (-y, x), 180: (-x, -y), 270: (y, -x)}.get(angle % 360)
    if cardinal is not None:
        return pivot[0] + cardinal[0], pivot[1] + cardinal[1]
    radians = math.radians(angle)
    c, s = math.cos(radians), math.sin(radians)
    return pivot[0] + x * c - y * s, pivot[1] + x * s + y * c


def transform_lines(lines: tuple, pivot: tuple, angle: float, offset: tuple) -> tuple:
    return tuple(
        tuple(
            (p[0] + offset[0], p[1] + offset[1])
            for p in (rotate_point(a, pivot, angle), rotate_point(b, pivot, angle))
        )
        for a, b in lines
    )


@lru_cache(maxsize=1024)
def guide_line(
    axis: str, position: float, size: tuple, rotation: float = 0, pivot: tuple | None = None
) -> Line | None:
    width, height = size
    center = pivot or (width / 2, height / 2)
    origin = (position, 0.0) if axis == "vertical" else (0.0, position)
    direction = (0.0, 1.0) if axis == "vertical" else (1.0, 0.0)
    a = rotate_point(origin, center, rotation)
    b = rotate_point((origin[0] + direction[0], origin[1] + direction[1]), center, rotation)
    dx, dy = b[0] - a[0], b[1] - a[1]
    hits = []
    for value in (0.0, float(width)):
        if abs(dx) > 1e-12:
            y = a[1] + (value - a[0]) * dy / dx
            if -1e-8 <= y <= height + 1e-8:
                hits.append((value, min(float(height), max(0.0, y))))
    for value in (0.0, float(height)):
        if abs(dy) > 1e-12:
            x = a[0] + (value - a[1]) * dx / dy
            if -1e-8 <= x <= width + 1e-8:
                hits.append((min(float(width), max(0.0, x)), value))
    unique = list(dict.fromkeys(hits))
    if len(unique) < 2:
        return None
    return max(((a, b) for a in unique for b in unique), key=lambda pair: math.dist(*pair))
