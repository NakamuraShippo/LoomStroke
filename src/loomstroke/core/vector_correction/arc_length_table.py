"""Cumulative arc length of a polyline -> normalised ``t`` per vertex."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

__all__ = ["ArcLengthTable"]

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class ArcLengthTable:
    cumulative: tuple[float, ...]

    @classmethod
    def build(cls, points: Sequence[Point], *, closed: bool = False) -> ArcLengthTable:
        lengths = [0.0]
        total = 0.0
        for (x0, y0), (x1, y1) in pairwise(points):
            total += math.hypot(x1 - x0, y1 - y0)
            lengths.append(total)
        if closed and len(points) > 1:
            total += math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1])
            lengths.append(total)
        return cls(tuple(lengths))

    @property
    def total(self) -> float:
        return self.cumulative[-1] if self.cumulative else 0.0

    def t_at(self, index: int) -> float:
        if self.total <= 1e-12 or not self.cumulative:
            return 0.0
        index = max(0, min(index, len(self.cumulative) - 1))
        return self.cumulative[index] / self.total

    def ts(self) -> tuple[float, ...]:
        return tuple(self.t_at(i) for i in range(len(self.cumulative)))
