"""Canvas coordinates carried by a cropped or prescaled filter input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FilterRasterContext:
    """Transient raster placement; never part of persisted filter parameters."""

    scale: int = 1
    origin: tuple[int, int] = (0, 0)
    canvas_size: tuple[int, int] | None = None

    @classmethod
    def from_layer(cls, layer: Any, canvas_size: tuple[int, int]) -> FilterRasterContext:
        scale = max(1, int(getattr(layer, "vector_raster_scale", 1) or 1))
        origin = tuple(getattr(layer, "vector_raster_origin", (0, 0)) or (0, 0))
        vector_raster = str(getattr(layer, "layer_type", "")) in {"vector", "text"}
        if scale == 1 and origin == (0, 0) and not vector_raster:
            return cls()
        return cls(scale, (int(origin[0]), int(origin[1])), canvas_size)

    def mask_uv(self, source: Any) -> tuple[float, float, float, float]:
        """UV origin and size mapping the crop back to the full carrier mask."""
        if self.canvas_size is None:
            return (0.0, 0.0, 1.0, 1.0)
        width, height = self.canvas_size
        return (
            self.origin[0] / width,
            self.origin[1] / height,
            source.width / (self.scale * width),
            source.height / (self.scale * height),
        )

    @property
    def texel_origin(self) -> tuple[int, int]:
        return (self.origin[0] * self.scale, self.origin[1] * self.scale)


DEFAULT_FILTER_RASTER_CONTEXT = FilterRasterContext()
