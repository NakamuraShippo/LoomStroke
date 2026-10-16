"""Evaluate and split a cubic without flattening its retained geometry."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class CubicStrokeSegment:
    controls: tuple[tuple[float, float], ...]

    @classmethod
    def from_anchors(cls, start: Any, end: Any) -> CubicStrokeSegment:
        a, d = start.pos, end.pos
        curved = bool(start.h_out or end.h_in)
        b = tuple(a[i] + start.h_out[i] for i in (0, 1)) if start.h_out else a
        c = tuple(d[i] + end.h_in[i] for i in (0, 1)) if end.h_in else d
        if not curved:
            b = tuple(a[i] + (d[i] - a[i]) / 3 for i in (0, 1))
            c = tuple(a[i] + (d[i] - a[i]) * 2 / 3 for i in (0, 1))
        return cls((a, b, c, d))

    @staticmethod
    def _lerp(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
        return tuple(a[i] + (b[i] - a[i]) * t for i in (0, 1))

    def split(self, t: float) -> tuple[CubicStrokeSegment, CubicStrokeSegment]:
        a, b, c, d = self.controls
        ab, bc, cd = self._lerp(a, b, t), self._lerp(b, c, t), self._lerp(c, d, t)
        abc, bcd = self._lerp(ab, bc, t), self._lerp(bc, cd, t)
        point = self._lerp(abc, bcd, t)
        return type(self)((a, ab, abc, point)), type(self)((point, bcd, cd, d))

    def point(self, t: float) -> tuple[float, float]:
        return self.split(t)[0].controls[-1]

    def slice(self, lower: float, upper: float) -> CubicStrokeSegment:
        left = self.split(upper)[0]
        return left.split(lower / upper)[1] if lower > 0 and upper > 0 else left

    def anchors(self, template: Any) -> tuple[Any, Any]:
        a, b, c, d = self.controls
        return (
            replace(template, pos=a, h_in=None, h_out=tuple(b[i] - a[i] for i in (0, 1))),
            replace(template, pos=d, h_out=None, h_in=tuple(c[i] - d[i] for i in (0, 1))),
        )
