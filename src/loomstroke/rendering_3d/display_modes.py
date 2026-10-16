"""3D preview display modes."""

from __future__ import annotations

from enum import Enum


class PreviewDisplayMode(str, Enum):
    """Preview mode toggles exposed by the 3D viewer."""

    SOLID = "solid"
    WIREFRAME = "wireframe"
    TEXTURED = "textured"
    NORMAL_DEBUG = "normal_debug"
