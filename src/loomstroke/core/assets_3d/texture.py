"""3D texture asset model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TextureAsset:
    """Texture reference used by a material or debug output map."""

    id: str
    path: Path | None = None
    usage: str = "base_color"
    color: tuple[int, int, int, int] | None = None
    normal_convention: str = "opengl"
    metadata: dict[str, Any] = field(default_factory=dict)
