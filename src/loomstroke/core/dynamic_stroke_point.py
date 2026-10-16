"""Immutable per-dab variation, separate from public tablet input types."""

from dataclasses import dataclass

from .pressure_stroke_point import PressureStrokePoint


@dataclass(frozen=True)
class DynamicStrokePoint(PressureStrokePoint):
    size_scale: float = 1.0
    tip_angle: float = 0.0
    dab_color: tuple[int, int, int] | None = None
