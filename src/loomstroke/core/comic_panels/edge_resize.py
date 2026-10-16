"""Source-anchor transforms for edited raster edges; never resample source pixels."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import PanelFrameError, Point, RasterAnchor, ResizePolicy


@dataclass(frozen=True, slots=True)
class EdgePlacement:
    """Affine coefficients mapping source-local coordinates to page pixels."""

    matrix: tuple[float, float, float, float, float, float]
    tangent_scale: float

    def map_point(self, point: Point) -> Point:
        a, b, c, d, tx, ty = self.matrix
        x, y = point
        return a * x + b * y + tx, c * x + d * y + ty


def edge_placement(
    anchor: RasterAnchor,
    start: Point,
    end: Point,
    *,
    policy: ResizePolicy = "keep_pixels",
) -> EdgePlacement:
    if len(start) != 2 or len(end) != 2 or not all(math.isfinite(v) for v in (*start, *end)):
        raise PanelFrameError("invalid edge endpoints")
    if policy not in {"keep_pixels", "stretch_tangent"}:
        raise PanelFrameError("unsupported raster resize policy")
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise PanelFrameError("cannot place raster onto a zero-length edge")
    ux, uy = dx / length, dy / length
    sx, sy = math.cos(anchor.source_angle_rad), math.sin(anchor.source_angle_rad)
    scale = length / anchor.base_edge_length_px if policy == "stretch_tangent" else 1.0
    # Rotate the original tangent basis onto the new edge, scaling only along it.
    a, b = scale * ux * sx + uy * sy, scale * ux * sy - uy * sx
    c, d = scale * uy * sx - ux * sy, scale * uy * sy + ux * sx
    px, py = start[0] + anchor.edge_t * dx, start[1] + anchor.edge_t * dy
    x, y = anchor.source_xy_px
    return EdgePlacement((a, b, c, d, px - a * x - b * y, py - c * x - d * y), scale)


def source_extent_on_edge(
    anchor: RasterAnchor,
    source_extent: tuple[float, float],
    new_length: float,
    *,
    policy: ResizePolicy = "keep_pixels",
) -> tuple[float, float]:
    """Nominal registered extent, including erased pixels; never scan the alpha."""
    if not math.isfinite(new_length) or new_length <= 0:
        raise PanelFrameError("invalid edge length")
    if (
        len(source_extent) != 2
        or not all(math.isfinite(v) for v in source_extent)
        or source_extent[1] < source_extent[0]
    ):
        raise PanelFrameError("invalid registered source extent")
    if policy not in {"keep_pixels", "stretch_tangent"}:
        raise PanelFrameError("unsupported raster resize policy")
    scale = new_length / anchor.base_edge_length_px if policy == "stretch_tangent" else 1.0
    return tuple(anchor.edge_t * new_length + v * scale for v in source_extent)
