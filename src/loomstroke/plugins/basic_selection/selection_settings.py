"""Scalar tool settings."""

from dataclasses import dataclass

from ..sdk.tool import ToolSettings


@dataclass
class SelectionSettings(ToolSettings):
    """Settings for the selection rectangle tool."""

    selection_mode: str = "Rectangle"
    operation: str = "Replace"
    pixel_perfect: bool = False
    feather_enabled: bool = False
    feather_radius: int = 0
    mask_radius: int = 8
    transform_x: float = 0.0
    transform_y: float = 0.0
    transform_scale_x: float = 1.0
    transform_scale_y: float = 1.0
    transform_rotation: float = 0.0
