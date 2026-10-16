"""Private pen width profile; tablet pressure and public stroke types stay intact."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .commands import StrokePoint


@dataclass(frozen=True)
class TaperStrokePoint(StrokePoint):
    distance: float = 0.0
    width_scale: float = 1.0


def point_width_scale(point: Any) -> float:
    value = float(getattr(point, "width_scale", 1.0))
    return max(0.0, min(1.0, value)) if math.isfinite(value) else 1.0


def taper_scale(distance: float, total: float | None, start: float, end: float) -> float:
    if total is not None and total <= 1e-6:
        return 1.0  # A click remains a usable dot.
    entry = min(1.0, distance / start) if start > 0.0 else 1.0
    exit_scale = min(1.0, max(0.0, total - distance) / end) if total is not None and end else 1.0
    return min(entry, exit_scale)
