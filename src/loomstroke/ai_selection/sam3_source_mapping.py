"""Source-image -> canvas coordinate mapping for SAM3 host sources (CT-23 §5 / §9.1)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["Sam3SourceMapping"]


@dataclass(frozen=True, slots=True)
class Sam3SourceMapping:
    origin_x: int = 0
    origin_y: int = 0
    width: int = 0
    height: int = 0
    scale: float = 1.0
    padding: int = 0
    crop_bounds: tuple[int, int, int, int] | None = None
    """Canvas-space ``(x, y, w, h)`` region the source pixels are limited to, if any."""

    @property
    def is_identity(self) -> bool:
        return self.origin_x == 0 and self.origin_y == 0 and abs(self.scale - 1.0) < 1e-9

    def to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return (self.origin_x + x * self.scale, self.origin_y + y * self.scale)

    def to_source(self, x: float, y: float) -> tuple[float, float]:
        scale = self.scale if self.scale > 1e-12 else 1.0
        return ((x - self.origin_x) / scale, (y - self.origin_y) / scale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "originX": int(self.origin_x),
            "originY": int(self.origin_y),
            "width": int(self.width),
            "height": int(self.height),
            "scale": float(self.scale),
            "padding": int(self.padding),
            "cropBounds": list(self.crop_bounds) if self.crop_bounds is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Sam3SourceMapping | None:
        if not isinstance(data, Mapping):
            return None
        try:
            raw_crop = data.get("cropBounds")
            crop = None
            if isinstance(raw_crop, (list, tuple)) and len(raw_crop) == 4:
                crop = (int(raw_crop[0]), int(raw_crop[1]), int(raw_crop[2]), int(raw_crop[3]))
            return cls(
                int(data.get("originX", 0)),
                int(data.get("originY", 0)),
                int(data.get("width", 0)),
                int(data.get("height", 0)),
                float(data.get("scale", 1.0)),
                int(data.get("padding", 0)),
                crop,
            )
        except (TypeError, ValueError):
            return None
