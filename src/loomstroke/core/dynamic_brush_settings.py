"""Private immutable settings for varied, textured, and mixing pen dabs."""

from dataclasses import dataclass

from .commands import BrushSettings


@dataclass(frozen=True)
class DynamicBrushSettings(BrushSettings):
    dab_size: float = 5.0
    hardness_mode: str = "radial"
    round_tip: bool = True
    texture_strength: float = 0.0
    texture_scale: float = 4.0
    variation_seed: int = 1
    color_mixing: float = 0.0
