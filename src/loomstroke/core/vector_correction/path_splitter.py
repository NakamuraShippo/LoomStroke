"""Knife geometry: split a polyline at ``(segment_index, t)`` into two open polylines."""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["PathSplitter"]

Point = tuple[float, float]


class PathSplitter:
    @staticmethod
    def split(
        points: Sequence[Point], segment_index: int, t: float
    ) -> tuple[tuple[Point, ...], tuple[Point, ...]] | None:
        count = len(points)
        if count < 2 or segment_index < 0 or segment_index >= count - 1:
            return None
        t = max(0.0, min(1.0, float(t)))
        a, b = points[segment_index], points[segment_index + 1]
        cut = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        left = [tuple(map(float, p)) for p in points[: segment_index + 1]]
        right = [tuple(map(float, p)) for p in points[segment_index + 1 :]]
        if t > 1e-9:
            left.append(cut)
        if t < 1.0 - 1e-9:
            right.insert(0, cut)
        if len(left) < 2 or len(right) < 2:
            return None
        return tuple(left), tuple(right)

    @staticmethod
    def split_many(
        points: Sequence[Point], cuts: Sequence[tuple[int, float]]
    ) -> tuple[tuple[Point, ...], ...]:
        """Split at several ``(segment_index, t)`` cuts (sorted, de-duplicated)."""
        pieces: list[tuple[Point, ...]] = []
        remaining: tuple[Point, ...] = tuple(tuple(map(float, p)) for p in points)
        consumed = 0
        for segment_index, t in sorted(set(cuts)):
            result = PathSplitter.split(remaining, segment_index - consumed, t)
            if result is None:
                continue
            left, right = result
            pieces.append(left)
            remaining = right
            consumed = segment_index
        pieces.append(remaining)
        return tuple(piece for piece in pieces if len(piece) >= 2)
