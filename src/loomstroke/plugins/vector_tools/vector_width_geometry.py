"""Arc-length picking for width handles, using the renderer's flattened curve."""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from loomstroke.core.vector_correction import ArcLengthTable
from loomstroke.core.vector_text_render_service import VectorTextRenderService

Point = tuple[float, float]


@dataclass(frozen=True)
class WidthPathGeometry:
    points: tuple[Point, ...]
    table: ArcLengthTable

    @classmethod
    def build(cls, obj: Any) -> WidthPathGeometry | None:
        # A profile belongs to the whole object. Reject compound objects rather than
        # unexpectedly changing sibling paths at the same normalised position.
        if len(obj.paths) != 1 or not 2 <= len(obj.paths[0].anchors) <= 4096:
            return None
        qpath = VectorTextRenderService.build_vector_qpaths(obj)[0]
        polygons = qpath.toSubpathPolygons()
        if len(polygons) != 1 or not 2 <= len(polygons[0]) <= 8192:
            return None
        points = tuple((p.x(), p.y()) for p in polygons[0])
        if not all(math.isfinite(v) for p in points for v in p):
            return None
        table = ArcLengthTable.build(points)
        return cls(points, table) if table.total > 1e-9 else None

    def at(self, t: float) -> tuple[Point, Point]:
        length = min(1.0, max(0.0, t)) * self.table.total
        index = min(len(self.points) - 2, bisect_right(self.table.cumulative, length) - 1)
        return self._segment(index, length)

    def _segment(self, index: int, length: float) -> tuple[Point, Point]:
        a, b = self.points[index : index + 2]
        dx, dy = b[0] - a[0], b[1] - a[1]
        distance = math.hypot(dx, dy)
        ratio = (length - self.table.cumulative[index]) / distance if distance > 1e-9 else 0
        center = (a[0] + dx * ratio, a[1] + dy * ratio)
        normal = (-dy / distance, dx / distance) if distance > 1e-9 else (0.0, 1.0)
        return center, normal

    def nearest(self, point: Point) -> tuple[float, Point, Point, float]:
        best = (math.inf, 0, 0.0)
        for index, (a, b) in enumerate(pairwise(self.points)):
            dx, dy = b[0] - a[0], b[1] - a[1]
            squared = dx * dx + dy * dy
            ratio = (
                min(1.0, max(0.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / squared))
                if squared > 1e-18
                else 0.0
            )
            distance = math.hypot(point[0] - a[0] - dx * ratio, point[1] - a[1] - dy * ratio)
            if distance < best[0]:
                best = (distance, index, ratio)
        distance, index, ratio = best
        length = self.table.cumulative[index] + ratio * (
            self.table.cumulative[index + 1] - self.table.cumulative[index]
        )
        center, normal = self._segment(index, length)
        return length / self.table.total, center, normal, distance
