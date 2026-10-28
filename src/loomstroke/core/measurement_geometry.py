"""Pure two-point measurement with independent document DPI axes."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .guide_layout_geometry import unit_to_pixels


@dataclass(frozen=True, slots=True)
class Measurement:
    distance: float
    angle: float
    dx: float
    dy: float
    unit: str


def measure_points(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    unit: str = "px",
    dpi: tuple[float, float] = (72.0, 72.0),
) -> Measurement:
    if any(not math.isfinite(v) for v in (*start, *end)):
        raise ValueError("Invalid measurement coordinates")
    dx, dy = end[0] - start[0], end[1] - start[1]
    ux = dx / unit_to_pixels(1.0, unit, dpi[0])
    uy = dy / unit_to_pixels(1.0, unit, dpi[1])
    return Measurement(math.hypot(ux, uy), math.degrees(math.atan2(dy, dx)), ux, uy, unit)
