"""3D material asset model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MaterialAsset:
    """Provider-neutral material description."""

    id: str
    name: str = "Material"
    base_color: tuple[float, float, float, float] = (0.72, 0.72, 0.72, 1.0)
    base_color_texture_id: str | None = None
    normal_texture_id: str | None = None
    roughness: float = 0.5
    metallic: float = 0.0
    alpha_mode: str = "opaque"
    metadata: dict[str, Any] = field(default_factory=dict)
