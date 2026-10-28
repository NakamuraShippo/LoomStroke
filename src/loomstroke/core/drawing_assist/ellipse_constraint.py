"""Rotated ellipse projection reusing the established concentric constraint."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .concentric_constraint import ConcentricConstraint

_UNIT_CIRCLE = ConcentricConstraint(0.0, 0.0)


@dataclass(frozen=True, slots=True)
class EllipseConstraint:
    center_x: float
    center_y: float
    radius_x: float
    radius_y: float
    cosine: float
    sine: float
    concentric: bool = True

    def _local(self, x: float, y: float) -> tuple[float, float]:
        dx, dy = x - self.center_x, y - self.center_y
        return dx * self.cosine + dy * self.sine, -dx * self.sine + dy * self.cosine

    def project(self, anchor: tuple[float, float], x: float, y: float) -> tuple[float, float]:
        local = self._local(x, y)
        start = self._local(*anchor) if self.concentric else (self.radius_x, 0.0)
        if not self.concentric and math.hypot(*local) < 1e-9:
            local = start
        nx, ny = _UNIT_CIRCLE.project(
            (start[0] / self.radius_x, start[1] / self.radius_y),
            local[0] / self.radius_x,
            local[1] / self.radius_y,
        )
        px, py = nx * self.radius_x, ny * self.radius_y
        return (
            self.center_x + px * self.cosine - py * self.sine,
            self.center_y + px * self.sine + py * self.cosine,
        )
