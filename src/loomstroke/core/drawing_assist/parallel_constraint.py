"""Constrain a stroke to the line through its first point with a fixed direction."""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["ParallelConstraint"]


@dataclass(frozen=True, slots=True)
class ParallelConstraint:
    dir_x: float
    dir_y: float

    @classmethod
    def from_angle(cls, angle_degrees: float) -> ParallelConstraint:
        radians = math.radians(angle_degrees)
        return cls(math.cos(radians), math.sin(radians))

    def project(self, anchor: tuple[float, float], x: float, y: float) -> tuple[float, float]:
        along = (x - anchor[0]) * self.dir_x + (y - anchor[1]) * self.dir_y
        return (anchor[0] + along * self.dir_x, anchor[1] + along * self.dir_y)
