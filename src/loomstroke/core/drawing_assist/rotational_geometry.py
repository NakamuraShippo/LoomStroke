"""N-fold rotation about a centre (rotational symmetry assist)."""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["RotationalGeometry"]


@dataclass(frozen=True, slots=True)
class RotationalGeometry:
    center_x: float
    center_y: float
    count: int
    mirror: bool = False
    """When set, every copy is also reflected (kaleidoscope / dihedral symmetry)."""

    def copies(self, x: float, y: float) -> tuple[tuple[float, float], ...]:
        """All *other* symmetric positions of ``(x, y)`` (count - 1 rotations, 2x with mirror)."""
        rx, ry = x - self.center_x, y - self.center_y
        out: list[tuple[float, float]] = []
        if self.mirror:
            out.append((self.center_x + rx, self.center_y - ry))
        for k in range(1, max(1, self.count)):
            angle = 2.0 * math.pi * k / self.count
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            qx, qy = rx * cos_a - ry * sin_a, rx * sin_a + ry * cos_a
            out.append((self.center_x + qx, self.center_y + qy))
            if self.mirror:
                out.append((self.center_x + qx, self.center_y - qy))
        return tuple(out)

    def spoke_angles(self) -> tuple[float, ...]:
        return tuple(2.0 * math.pi * k / self.count for k in range(max(1, self.count)))
