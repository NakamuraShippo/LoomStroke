"""Independent size and opacity pressure curves for the pen."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class PressureResponseSettings:
    size_pressure_gamma: float = 1.0
    size_pressure_minimum: float = 0.0
    opacity_pressure_enabled: bool = False
    opacity_pressure_gamma: float = 1.0
    opacity_pressure_minimum: float = 0.0

    def values(self) -> dict[str, Any]:
        return asdict(self)

    def update(self, payload: dict[str, Any]) -> None:
        for key in self.values():
            if key not in payload:
                continue
            if key == "opacity_pressure_enabled":
                setattr(self, key, bool(payload[key]))
                continue
            try:
                value = float(payload[key])
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(value):
                lower, upper = (0.1, 4.0) if key.endswith("gamma") else (0.0, 1.0)
                setattr(self, key, min(upper, max(lower, value)))

    def response(self, pressure: float, *, enabled: bool) -> tuple[float, float, float]:
        raw = min(1.0, max(0.0, pressure)) if math.isfinite(pressure) else 1.0
        if not enabled:
            return 1.0, 1.0, 1.0
        size = (
            self.size_pressure_minimum
            + (1.0 - self.size_pressure_minimum) * raw**self.size_pressure_gamma
        )
        opacity = 1.0
        if self.opacity_pressure_enabled:
            opacity = (
                self.opacity_pressure_minimum
                + (1.0 - self.opacity_pressure_minimum) * raw**self.opacity_pressure_gamma
            )
        return size, opacity, raw
