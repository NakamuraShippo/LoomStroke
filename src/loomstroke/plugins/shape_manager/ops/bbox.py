from __future__ import annotations

import math
from collections.abc import Iterable

from ...vector_tools.common import VectorObject, VectorPath


def compute_bbox(paths: Iterable[VectorPath]) -> tuple[float, float, float, float] | None:
    """Compute tight bounding box using cubic bezier extrema (anchors + segments)."""
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    for path in paths:
        anchors = path.anchors
        n = len(anchors)
        if n == 0:
            continue
        # Include all anchor positions
        for anchor in anchors:
            min_x = min(min_x, anchor.pos[0])
            min_y = min(min_y, anchor.pos[1])
            max_x = max(max_x, anchor.pos[0])
            max_y = max(max_y, anchor.pos[1])
        # Process segments between consecutive anchors
        seg_count = n if path.closed else n - 1
        for i in range(seg_count):
            a0 = anchors[i]
            a1 = anchors[(i + 1) % n]
            h_out = a0.h_out
            h_in = a1.h_in
            if h_out is None and h_in is None:
                continue  # straight line - endpoints already included
            p0x, p0y = a0.pos
            p3x, p3y = a1.pos
            p1x = p0x + h_out[0] if h_out else p0x
            p1y = p0y + h_out[1] if h_out else p0y
            p2x = p3x + h_in[0] if h_in else p3x
            p2y = p3y + h_in[1] if h_in else p3y
            for t in _cubic_extrema_t(p0x, p1x, p2x, p3x):
                x = _eval_cubic(t, p0x, p1x, p2x, p3x)
                min_x = min(min_x, x)
                max_x = max(max_x, x)
            for t in _cubic_extrema_t(p0y, p1y, p2y, p3y):
                y = _eval_cubic(t, p0y, p1y, p2y, p3y)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if min_x == float("inf"):
        return None
    return (min_x, min_y, max_x, max_y)


def compute_object_bbox(obj: VectorObject) -> tuple[float, float, float, float] | None:
    return compute_bbox(obj.paths)


def merge_bboxes(
    bboxes: Iterable[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    found = False
    for bbox in bboxes:
        found = True
        min_x = min(min_x, bbox[0])
        min_y = min(min_y, bbox[1])
        max_x = max(max_x, bbox[2])
        max_y = max(max_y, bbox[3])
    if not found:
        return None
    return (min_x, min_y, max_x, max_y)


# -- Cubic bezier utilities --------------------------------------------------


def _cubic_extrema_t(p0: float, p1: float, p2: float, p3: float) -> list[float]:
    """Return parameter values t in (0,1) where the cubic bezier has extrema.

    B(t)  = (1-t)^3*p0 + 3*(1-t)^2*t*p1 + 3*(1-t)*t^2*p2 + t^3*p3
    B'(t) = 3*[a*t^2 + b*t + c]
    where  a = p3 - 3*p2 + 3*p1 - p0
           b = 2*(p2 - 2*p1 + p0)
           c = p1 - p0
    """
    a = p3 - 3.0 * p2 + 3.0 * p1 - p0
    b = 2.0 * (p2 - 2.0 * p1 + p0)
    c = p1 - p0
    ts: list[float] = []
    if abs(a) < 1e-12:
        # Degenerate to linear: b*t + c = 0
        if abs(b) > 1e-12:
            t = -c / b
            if 0.0 < t < 1.0:
                ts.append(t)
    else:
        disc = b * b - 4.0 * a * c
        if disc >= 0.0:
            sq = math.sqrt(disc)
            for t in ((-b + sq) / (2.0 * a), (-b - sq) / (2.0 * a)):
                if 0.0 < t < 1.0:
                    ts.append(t)
    return ts


def _eval_cubic(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """Evaluate cubic bezier at parameter *t*."""
    u = 1.0 - t
    return u * u * u * p0 + 3.0 * u * u * t * p1 + 3.0 * u * t * t * p2 + t * t * t * p3
