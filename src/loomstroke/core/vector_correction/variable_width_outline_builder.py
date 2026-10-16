"""Polyline + per-vertex width -> fillable outline polygons (backend neutral, CT-15 MVP).

Open paths yield one closed polygon (left side, end cap, right side back, start
cap); closed paths yield ``(outer_ring, inner_ring)`` to be filled odd-even.
Joins use the averaged normal (miter, clamped to ``miter_limit``), caps are round.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import ClassVar

__all__ = ["VariableWidthOutlineBuilder"]

Point = tuple[float, float]
Polygon = tuple[Point, ...]


class VariableWidthOutlineBuilder:
    MIN_HALF_WIDTH: ClassVar[float] = 0.05
    CAP_STEPS: ClassVar[int] = 8
    MAX_POINTS: ClassVar[int] = 200_000

    def __init__(self, *, miter_limit: float = 4.0, round_caps: bool = True) -> None:
        self._miter_limit = max(1.0, float(miter_limit))
        self._round_caps = round_caps

    def build(
        self, points: Sequence[Point], widths: Sequence[float], *, closed: bool = False
    ) -> tuple[Polygon, ...]:
        pts = self._dedupe(points)
        if len(pts) < 2 or len(pts) > self.MAX_POINTS:
            return ()
        halves = self._half_widths(points, widths, pts)
        normals = self._vertex_normals(pts, closed)
        left = tuple(
            (p[0] + n[0] * h, p[1] + n[1] * h) for p, n, h in zip(pts, normals, halves, strict=True)
        )
        right = tuple(
            (p[0] - n[0] * h, p[1] - n[1] * h) for p, n, h in zip(pts, normals, halves, strict=True)
        )
        if closed:
            return (left, right)
        end_cap = self._cap(pts[-1], pts[-2], halves[-1])
        start_cap = self._cap(pts[0], pts[1], halves[0])
        return ((*left, *end_cap, *reversed(right), *start_cap),)

    # -- internals ---------------------------------------------------------------------

    @staticmethod
    def _dedupe(points: Sequence[Point]) -> list[Point]:
        out: list[Point] = []
        for x, y in points:
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            if out and abs(out[-1][0] - x) < 1e-9 and abs(out[-1][1] - y) < 1e-9:
                continue
            out.append((float(x), float(y)))
        return out

    def _half_widths(
        self, original: Sequence[Point], widths: Sequence[float], pts: list[Point]
    ) -> list[float]:
        if len(widths) == len(pts):
            source = list(widths)
        elif widths:
            # Resample the width list onto the de-duplicated point count.
            last = len(widths) - 1
            source = [
                float(widths[round(i * last / max(1, len(pts) - 1))]) for i in range(len(pts))
            ]
        else:
            source = [1.0] * len(pts)
        return [max(self.MIN_HALF_WIDTH, float(w) / 2.0) for w in source]

    def _vertex_normals(self, pts: list[Point], closed: bool) -> list[Point]:
        count = len(pts)
        segment_normals: list[Point] = []
        for i in range(count - 1):
            segment_normals.append(self._normal(pts[i], pts[i + 1]))
        if closed:
            segment_normals.append(self._normal(pts[-1], pts[0]))
        normals: list[Point] = []
        for i in range(count):
            if closed:
                prev_n = segment_normals[(i - 1) % len(segment_normals)]
                next_n = segment_normals[i % len(segment_normals)]
            elif i == 0:
                normals.append(segment_normals[0])
                continue
            elif i == count - 1:
                normals.append(segment_normals[-1])
                continue
            else:
                prev_n, next_n = segment_normals[i - 1], segment_normals[i]
            normals.append(self._join_normal(prev_n, next_n))
        return normals

    @staticmethod
    def _normal(a: Point, b: Point) -> Point:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-12:
            return (0.0, 0.0)
        return (-dy / length, dx / length)

    def _join_normal(self, prev_n: Point, next_n: Point) -> Point:
        mx, my = prev_n[0] + next_n[0], prev_n[1] + next_n[1]
        length = math.hypot(mx, my)
        if length < 1e-9:
            return next_n  # 180 degree turn: fall back to the next segment normal
        mx, my = mx / length, my / length
        cos_half = mx * next_n[0] + my * next_n[1]
        if cos_half < 1e-6:
            return next_n
        scale = min(1.0 / cos_half, self._miter_limit)
        return (mx * scale, my * scale)

    def _cap(self, tip: Point, inner: Point, half: float) -> tuple[Point, ...]:
        if not self._round_caps:
            return ()
        dx, dy = tip[0] - inner[0], tip[1] - inner[1]
        length = math.hypot(dx, dy)
        if length < 1e-12:
            return ()
        angle = math.atan2(dy, dx)
        steps = self.CAP_STEPS
        # Sweep from the left normal over the tip to the right normal.
        return tuple(
            (
                tip[0] + math.cos(angle - math.pi / 2.0 + math.pi * k / steps) * half,
                tip[1] + math.sin(angle - math.pi / 2.0 + math.pi * k / steps) * half,
            )
            for k in range(1, steps)
        )
