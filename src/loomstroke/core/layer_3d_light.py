"""Scene-space position used by the editable 3D layer point light."""

from __future__ import annotations

import math
from typing import Any

from loomstroke.core.assets_3d.scene import Scene3DDocument


def light_position(layer: Any) -> tuple[float, float, float]:
    lighting = getattr(layer, "lighting_3d", None)
    value = lighting.get("position") if isinstance(lighting, dict) else None
    try:
        position = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        position = ()
    if len(position) == 3 and all(map(math.isfinite, position)):
        return position
    scene = getattr(layer, "scene_3d", None)
    if not isinstance(scene, Scene3DDocument):
        return (0.0, 0.0, 0.0)
    low, high = scene.bounds()
    center = (low + high) * 0.5
    radius = max(float(((high - low) ** 2).sum() ** 0.5) * 0.5, 1.0)
    return (float(center[0] - radius), float(center[1] - radius), float(center[2] + radius * 2))
