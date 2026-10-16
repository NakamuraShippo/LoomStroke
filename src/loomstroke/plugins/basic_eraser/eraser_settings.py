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
