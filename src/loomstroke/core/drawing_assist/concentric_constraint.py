"""Constrain a stroke to the circle / ellipse through its first point (concentric assist)."""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["ConcentricConstraint"]


@dataclass(frozen=True, slots=True)
class ConcentricConstraint:
    center_x: float
    center_y: float
    ratio: float = 1.0
    """``ry / rx``: 1.0 for circles."""

    def project(self, anchor: tuple[float, float], x: float, y: float) -> tuple[float, float]:
        ratio = self.ratio if self.ratio > 1e-6 else 1.0
        # Work in a space where the ellipse is a circle.
        ax, ay = anchor[0] - self.center_x, (anchor[1] - self.center_y) / ratio
        radius = math.hypot(ax, ay)
        if radius < 1e-9:
            return (self.center_x, self.center_y)
        px, py = x - self.center_x, (y - self.center_y) / ratio
        length = math.hypot(px, py)
        if length < 1e-9:
            return anchor
        qx, qy = px / length * radius, py / length * radius
        return (self.center_x + qx, self.center_y + qy * ratio)

    def radius_through(self, anchor: tuple[float, float]) -> float:
        ratio = self.ratio if self.ratio > 1e-6 else 1.0
        return math.hypot(anchor[0] - self.center_x, (anchor[1] - self.center_y) / ratio)
