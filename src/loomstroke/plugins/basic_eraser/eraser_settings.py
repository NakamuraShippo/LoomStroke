"""Scalar tool settings."""

from dataclasses import dataclass

from ..sdk.tool import ToolSettings


@dataclass
class EraserSettings(ToolSettings):
    """Settings for the basic eraser tool."""

    size: float = 20.0
    opacity: float = 1.0  # Erase strength
    hardness: float = 0.5
    spacing: float = 0.1
    anti_alias: bool = True
    vector_erase_mode: str = "Touched Area"
    # Normal cuts paint away; Kneaded lifts it a little at a time (raster layers).
    eraser_type: str = "Normal"
    # Kneaded only: how much paint the lump holds before it lifts less (0..100).
    durability: float = 50.0
