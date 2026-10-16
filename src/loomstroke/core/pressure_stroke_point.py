"""Private pen response data carried alongside the existing stroke contract."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .pen_taper_model import TaperStrokePoint


@dataclass(frozen=True)
class PressureStrokePoint(TaperStrokePoint):
    opacity_scale: float = 1.0
    tone_pressure: float = 1.0


def point_opacity_scale(point: Any) -> float:
    value = float(getattr(point, "opacity_scale", 1.0))
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 1.0


def point_tone_pressure(point: Any) -> float:
    return float(getattr(point, "tone_pressure", getattr(point, "pressure", 1.0)))
