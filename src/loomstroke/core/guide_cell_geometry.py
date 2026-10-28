"""Pure raster cell bounds for adjacent horizontal/vertical guides."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable

from .guide_layout_geometry import GuidePosition


def guide_cell_rect(
    guides: Iterable[GuidePosition],
    size: tuple[int, int],
    point: tuple[float, float],
) -> tuple[int, int, int, int] | None:
    width, height = size
    x, y = point
    if min(width, height) <= 0 or not all(math.isfinite(v) for v in (*size, *point)):
        return None
    if not 0 <= x <= width or not 0 <= y <= height:
        return None
    xs, ys = {0.0, float(width)}, {0.0, float(height)}
    found = False
    for axis, value in guides:
        extent = width if axis == "vertical" else height
        if (
            axis not in ("vertical", "horizontal")
            or not math.isfinite(value)
            or not 0 <= value <= extent
        ):
            continue
        (xs if axis == "vertical" else ys).add(value)
        found = True
    if not found:
        return None
    axes = []
    for values, coordinate in ((xs, x), (ys, y)):
        ordered = sorted(values)
        index = min(bisect_right(ordered, coordinate) - 1, len(ordered) - 2)
        axes.append((math.floor(ordered[index]), math.ceil(ordered[index + 1])))
    (x0, x1), (y0, y1) = axes
    return x0, y0, x1 - x0, y1 - y0
