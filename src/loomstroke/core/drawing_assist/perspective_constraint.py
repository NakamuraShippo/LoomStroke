"""Constrain a stroke to a line towards a vanishing point (1-3 point perspective).

``axis`` picks the vanishing point explicitly (``vp1`` / ``vp2`` / ``vp3``),
``vertical`` forces vertical lines, and ``auto`` chooses the vanishing point
whose direction best matches the stroke's initial motion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["PerspectiveConstraint"]


@dataclass(frozen=True, slots=True)
class PerspectiveConstraint:
    vanishing_points: tuple[tuple[float, float], ...]
    axis: str = "auto"

    def project(self, anchor: tuple[float, float], x: float, y: float) -> tuple[float, float]:
        if self.axis == "vertical" or not self.vanishing_points:
            return (anchor[0], y)
        target = self._vanishing_point_for(anchor, x, y)
        dx, dy = target[0] - anchor[0], target[1] - anchor[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return anchor
        ux, uy = dx / length, dy / length
        along = (x - anchor[0]) * ux + (y - anchor[1]) * uy
        return (anchor[0] + along * ux, anchor[1] + along * uy)

    def _vanishing_point_for(
        self, anchor: tuple[float, float], x: float, y: float
    ) -> tuple[float, float]:
        if self.axis.startswith("vp"):
            try:
                index = int(self.axis[2:]) - 1
            except ValueError:
                index = 0
            return self.vanishing_points[max(0, min(index, len(self.vanishing_points) - 1))]
        mx, my = x - anchor[0], y - anchor[1]
        motion = math.hypot(mx, my)
        if motion < 1e-9:
            return self.vanishing_points[0]
        best = self.vanishing_points[0]
        best_score = -2.0
        for point in self.vanishing_points:
            dx, dy = point[0] - anchor[0], point[1] - anchor[1]
            length = math.hypot(dx, dy)
            if length < 1e-9:
                continue
            score = abs((mx * dx + my * dy) / (motion * length))
            if score > best_score:
                best, best_score = point, score
        return best
