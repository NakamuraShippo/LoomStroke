"""Pure trim/bleed inspection and channel-preserving raster edge extension."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from .guide_layout_geometry import guide_role_edges

MAX_BLEED_PIXELS = 32 * 1024 * 1024
Bounds = tuple[float, float, float, float]


def trim_bounds(guides: Sequence[Mapping[str, Any]], size: tuple[int, int]) -> Bounds | None:
    try:
        left, right = guide_role_edges(guides, "trim", "vertical")
        top, bottom = guide_role_edges(guides, "trim", "horizontal")
    except (ValueError, TypeError, KeyError):
        return None
    if not 0 <= left < right <= size[0] or not 0 <= top < bottom <= size[1]:
        return None
    return left, top, right, bottom


def bleed_short_sides(content: Bounds, trim: Bounds, size: tuple[int, int]) -> tuple[str, ...]:
    x, y, width, height = content
    left, top, right, bottom = trim
    sides = []
    if x <= left < x + width and x > 0:
        sides.append("left")
    if x < right <= x + width and x + width < size[0]:
        sides.append("right")
    if y <= top < y + height and y > 0:
        sides.append("top")
    if y < bottom <= y + height and y + height < size[1]:
        sides.append("bottom")
    return tuple(sides)


def _source_indices(indices: np.ndarray, first: int, last: int, method: str) -> np.ndarray:
    if method == "edge":
        return np.clip(indices, first, last - 1)
    width = last - first
    offset = (indices - first) % (width * 2)
    return first + np.where(offset < width, offset, width * 2 - 1 - offset)


def extend_bleed_pixels(pixels: np.ndarray, trim: Bounds, method: str) -> np.ndarray | None:
    if pixels.ndim != 3 or pixels.shape[2] != 4 or method not in ("edge", "mirror"):
        raise ValueError("Unsupported bleed extension")
    height, width = pixels.shape[:2]
    if width * height > MAX_BLEED_PIXELS:
        raise ValueError("Bleed extension exceeds the synchronous pixel budget")
    left, top, right, bottom = trim
    if (
        not all(math.isfinite(v) for v in trim)
        or not 0 <= left < right <= width
        or not 0 <= top < bottom <= height
    ):
        raise ValueError("Invalid trim bounds")
    x0, y0, x1, y1 = (math.ceil(v - 0.5) for v in trim)
    if x0 >= x1 or y0 >= y1:
        raise ValueError("Trim contains no pixel centers")
    regions = ((0, 0, width, y0), (0, y1, width, height), (0, y0, x0, y1), (x1, y0, width, y1))
    result = None
    for a, b, c, d in regions:
        if a == c or b == d:
            continue
        xs = _source_indices(np.arange(a, c), x0, x1, method)
        ys = _source_indices(np.arange(b, d), y0, y1, method)
        candidate = pixels[ys[:, None], xs[None, :]]
        if np.array_equal(pixels[b:d, a:c], candidate):
            continue
        if result is None:
            result = pixels.copy()
        result[b:d, a:c] = candidate
    return result
