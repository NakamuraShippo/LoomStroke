"""Qt-free binding danger rectangles, in page-space edge coordinates."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .geometry import Affine2D
from .guide_layout_geometry import guide_role_edges, is_unrotated_guide

Bounds = tuple[float, float, float, float]


def binding_danger_bounds(guides: Sequence[Mapping[str, Any]], physical_side: str) -> Bounds | None:
    if physical_side not in ("left", "right"):
        return None
    entries = [
        g
        for g in guides
        if str(g.get("id", "")).startswith("tpl-gutter-") and is_unrotated_guide(g)
    ]
    if len(entries) != 1 or entries[0].get("orientation") != "vertical":
        return None
    try:
        left, right = guide_role_edges(guides, "trim", "vertical")
        top, bottom = guide_role_edges(guides, "trim", "horizontal")
        gutter = float(entries[0]["position"])
    except (ValueError, TypeError, KeyError):
        return None
    if not math.isfinite(gutter) or not left < gutter < right or not top < bottom:
        return None
    return (gutter, top, right, bottom) if physical_side == "left" else (left, top, gutter, bottom)


def bounds_intersect(first: Bounds, second: Bounds) -> bool:
    return (
        all(math.isfinite(v) for v in (*first, *second))
        and max(first[0], second[0]) < min(first[2], second[2])
        and max(first[1], second[1]) < min(first[3], second[3])
    )


def rotated_bounds(bounds: Bounds, degrees: float) -> Bounds:
    left, top, right, bottom = bounds
    cx, cy = (left + right) / 2, (top + bottom) / 2
    sine, cosine = math.sin(math.radians(degrees)), math.cos(math.radians(degrees))
    points = [
        (cx + (x - cx) * cosine - (y - cy) * sine, cy + (x - cx) * sine + (y - cy) * cosine)
        for x in (left, right)
        for y in (top, bottom)
    ]
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )


def transformed_bounds(bounds: Bounds, transform: Affine2D) -> Bounds:
    left, top, right, bottom = bounds
    points = [transform.apply(x, y) for x in (left, right) for y in (top, bottom)]
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )
