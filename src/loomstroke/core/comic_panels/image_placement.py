"""Original image pixels and an explicit cover/contain placement transform."""

from __future__ import annotations

import math
from dataclasses import dataclass

from loomstroke.core.command_types import LayerTransform

from .models import PanelFrameError, Rect, validate_rect

PLACEMENT_KEY = "loomstroke.comictools.placement"


@dataclass(frozen=True, slots=True)
class ImagePlacement:
    source_size: tuple[int, int]
    transform: LayerTransform
    fit_policy: str

    @property
    def bounds(self) -> Rect:
        t = self.transform
        return t.tx, t.ty, self.source_size[0] * t.scale_x, self.source_size[1] * t.scale_y


def fit_image(
    source_size: tuple[int, int],
    rect: Rect,
    *,
    policy: str = "cover",
    focal: tuple[float, float] = (0.5, 0.5),
) -> ImagePlacement:
    validate_rect(rect)
    if len(source_size) != 2 or any(type(v) is not int or v <= 0 for v in source_size):
        raise PanelFrameError("image source has invalid dimensions")
    if policy not in {"cover", "contain"}:
        raise PanelFrameError("unsupported image fit policy")
    if len(focal) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in focal):
        raise PanelFrameError("invalid image focal point")
    x, y, w, h = rect
    sw, sh = source_size
    factor = (max if policy == "cover" else min)(w / sw, h / sh)
    return ImagePlacement(
        source_size,
        LayerTransform(
            tx=x + (w - sw * factor) * focal[0],
            ty=y + (h - sh * factor) * focal[1],
            scale_x=factor,
            scale_y=factor,
        ),
        policy,
    )
