"""Project onto a vanishing-point line or a parallel axis through the stroke start."""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["PerspectiveConstraint"]


@dataclass(frozen=True, slots=True)
class PerspectiveConstraint:
    vanishing_points: tuple[tuple[float, float], ...]
    axis: str = "auto"

    def axis_for_motion(self, anchor: tuple[float, float], x: float, y: float) -> str:
        """Choose the closest line, including parallel axes for 1/2-point views."""
        mx, my = x - anchor[0], y - anchor[1]
        count = len(self.vanishing_points)
        best_axis, best_score = ("vp1" if count else "vertical"), -1.0
        for index, (vx, vy) in enumerate(self.vanishing_points, 1):
            dx, dy = vx - anchor[0], vy - anchor[1]
            length = math.hypot(dx, dy)
            if length > 1e-9:
                score = abs((mx * dx + my * dy) / length)
                if score > best_score:
                    best_axis, best_score = f"vp{index}", score
        if count == 1 and abs(mx) > best_score:
            best_axis, best_score = "horizontal", abs(mx)
        if count < 3 and abs(my) > best_score:
            best_axis = "vertical"
        return best_axis

    def project(self, anchor: tuple[float, float], x: float, y: float) -> tuple[float, float]:
        axis = self.axis
        if axis not in ("horizontal", "vertical") and not axis.startswith("vp"):
            axis = self.axis_for_motion(anchor, x, y)
        if axis == "vertical" or not self.vanishing_points:
            return (anchor[0], y)
        if axis == "horizontal":
            return (x, anchor[1])
        try:
            index = int(axis[2:]) - 1
        except ValueError:
            index = 0
        target = self.vanishing_points[max(0, min(index, len(self.vanishing_points) - 1))]
        dx, dy = target[0] - anchor[0], target[1] - anchor[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return anchor
        ux, uy = dx / length, dy / length
        along = (x - anchor[0]) * ux + (y - anchor[1]) * uy
        return (anchor[0] + along * ux, anchor[1] + along * uy)
