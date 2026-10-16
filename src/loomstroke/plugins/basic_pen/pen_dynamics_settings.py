"""Validated advanced brush settings without changing legacy pen presets."""

import math
from dataclasses import asdict, dataclass


@dataclass
class PenDynamicsSettings:
    scatter: float = 0.0
    size_variation: float = 0.0
    angle_variation: float = 0.0
    color_variation: float = 0.0
    texture_strength: float = 0.0
    texture_scale: float = 4.0
    color_mixing: float = 0.0
    variation_seed: int = 1

    @property
    def enabled(self) -> bool:
        return any(
            value > 0
            for key, value in self.values().items()
            if key not in {"texture_scale", "variation_seed"}
        )

    def values(self) -> dict:
        return asdict(self)

    def update(self, settings: dict) -> None:
        for key in self.values():
            if key not in settings:
                continue
            try:
                value = float(settings[key])
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(value):
                continue
            lower, upper = (
                (1, 64) if key == "texture_scale" else (0, 3) if key == "scatter" else (0, 1)
            )
            if key == "variation_seed":
                setattr(self, key, int(value) % 2**32)
            else:
                setattr(self, key, min(upper, max(lower, value)))
