"""Segment / segment and segment / polyline intersection (bounded, no O(n²) surprises)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

__all__ = ["SegmentIntersection", "SegmentIntersector"]

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class SegmentIntersection:
    t: float
    """Parameter on the first segment (0..1)."""
    u: float
    """Parameter on the second segment (0..1)."""
    point: Point
    segment_index: int = 0
    """Index of the polyline segment hit (polyline queries only)."""


class SegmentIntersector:
    MAX_SEGMENTS: ClassVar[int] = 50_000
    EPS: ClassVar[float] = 1e-12

    @classmethod
    def intersect(cls, p1: Point, p2: Point, q1: Point, q2: Point) -> SegmentIntersection | None:
        rx, ry = p2[0] - p1[0], p2[1] - p1[1]
        sx, sy = q2[0] - q1[0], q2[1] - q1[1]
        denominator = rx * sy - ry * sx
        if abs(denominator) < cls.EPS:
            return None
        qpx, qpy = q1[0] - p1[0], q1[1] - p1[1]
        t = (qpx * sy - qpy * sx) / denominator
        u = (qpx * ry - qpy * rx) / denominator
        if -cls.EPS <= t <= 1.0 + cls.EPS and -cls.EPS <= u <= 1.0 + cls.EPS:
            t, u = max(0.0, min(1.0, t)), max(0.0, min(1.0, u))
            return SegmentIntersection(t, u, (p1[0] + t * rx, p1[1] + t * ry))
        return None

    @classmethod
    def with_polyline(
        cls, p1: Point, p2: Point, polyline: Sequence[Point], *, closed: bool = False
    ) -> tuple[SegmentIntersection, ...]:
        """All intersections of segment ``p1-p2`` with *polyline*, ordered by ``t``."""
        count = len(polyline)
        if count < 2 or count > cls.MAX_SEGMENTS:
            return ()
        hits: list[SegmentIntersection] = []
        last = count if closed else count - 1
        for index in range(last):
            q1, q2 = polyline[index], polyline[(index + 1) % count]
            hit = cls.intersect(p1, p2, q1, q2)
            if hit is not None:
                hits.append(SegmentIntersection(hit.t, hit.u, hit.point, index))
        return tuple(sorted(hits, key=lambda h: h.t))
