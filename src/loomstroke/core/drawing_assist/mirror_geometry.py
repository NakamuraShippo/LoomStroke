"""Reflection across the line through two page points (mirror assist)."""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["MirrorGeometry"]


@dataclass(frozen=True, slots=True)
class MirrorGeometry:
    origin_x: float
    origin_y: float
    dir_x: float
    dir_y: float

    @classmethod
    def from_points(cls, p0: tuple[float, float], p1: tuple[float, float]) -> MirrorGeometry | None:
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < 1e-9 or not math.isfinite(length):
            return None
        return cls(p0[0], p0[1], dx / length, dy / length)

    def reflect(self, x: float, y: float) -> tuple[float, float]:
        rx, ry = x - self.origin_x, y - self.origin_y
        along = rx * self.dir_x + ry * self.dir_y
        px, py = self.origin_x + along * self.dir_x, self.origin_y + along * self.dir_y
        return (2.0 * px - x, 2.0 * py - y)

    def side(self, x: float, y: float) -> float:
        """Signed distance: positive on the left of the axis direction."""
        rx, ry = x - self.origin_x, y - self.origin_y
        return self.dir_x * ry - self.dir_y * rx
