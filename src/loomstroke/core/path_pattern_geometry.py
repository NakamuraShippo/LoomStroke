"""Even arc-distance pattern poses using existing curve and length primitives."""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass
from itertools import pairwise

from .vector_correction.arc_length_table import ArcLengthTable
from .vector_correction.cubic_stroke_segment import CubicStrokeSegment


@dataclass(frozen=True)
class PatternPose:
    x: float
    y: float
    angle: float


def pattern_poses(
    anchors, *, closed=False, spacing=32.0, follow=True, variation=0.0, seed=20261028
) -> tuple[PatternPose, ...]:
    if (
        not math.isfinite(spacing)
        or spacing <= 0
        or not math.isfinite(variation)
        or not 0 <= variation <= 1
    ):
        raise ValueError("Invalid pattern settings")
    count = len(anchors)
    if count < 2 or count > 65 or (closed and count > 64):
        raise ValueError("Unsupported path size")
    points = []
    ends = list(pairwise(anchors))
    if closed:
        ends.append((anchors[-1], anchors[0]))
    for start, end in ends:
        segment = CubicStrokeSegment.from_anchors(start, end)
        if not all(math.isfinite(v) for p in segment.controls for v in p):
            raise ValueError("Invalid curve")
        points.extend(segment.point(i / 32) for i in range(32))
    points.append(CubicStrokeSegment.from_anchors(*ends[-1]).point(1))
    distinct = []
    for point in points:
        if (
            not distinct
            or math.hypot(point[0] - distinct[-1][0], point[1] - distinct[-1][1]) > 1e-12
        ):
            distinct.append(point)
    points = distinct
    table = ArcLengthTable.build(points)
    if not math.isfinite(table.total) or table.total < 1e-9:
        raise ValueError("Degenerate path")
    number = math.ceil(table.total / spacing) if closed else math.floor(table.total / spacing) + 1
    if number > 512:
        raise ValueError("Too many pattern instances")
    rng = random.Random(seed)
    poses = []
    for i in range(number):
        distance = min(table.total, i * spacing)
        right = min(len(points) - 1, max(1, bisect.bisect_right(table.cumulative, distance)))
        left = right - 1
        segment_length = table.cumulative[right] - table.cumulative[left]
        t = (distance - table.cumulative[left]) / segment_length if segment_length > 1e-12 else 0
        a, b = points[left], points[right]
        angle = math.atan2(b[1] - a[1], b[0] - a[0])
        offset = (rng.random() - 0.5) * spacing * variation
        x = a[0] + (b[0] - a[0]) * t - math.sin(angle) * offset
        y = a[1] + (b[1] - a[1]) * t + math.cos(angle) * offset
        rotation = angle if follow else 0
        rotation += (rng.random() - 0.5) * math.pi / 3 * variation
        poses.append(PatternPose(x, y, rotation))
    return tuple(poses)
