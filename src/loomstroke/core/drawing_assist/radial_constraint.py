"""Constrain a stroke to the ray from a centre through the stroke's first point."""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["RadialConstraint"]


@dataclass(frozen=True, slots=True)
class RadialConstraint:
    center_x: float
    center_y: float

    def project(self, anchor: tuple[float, float], x: float, y: float) -> tuple[float, float]:
        dx, dy = anchor[0] - self.center_x, anchor[1] - self.center_y
        length = math.hypot(dx, dy)
        if length < 1e-9:
            # Anchor on the centre: fall back to the direction towards the point.
            dx, dy = x - self.center_x, y - self.center_y
            length = math.hypot(dx, dy)
            if length < 1e-9:
                return (self.center_x, self.center_y)
        ux, uy = dx / length, dy / length
        along = (x - self.center_x) * ux + (y - self.center_y) * uy
        return (self.center_x + along * ux, self.center_y + along * uy)
