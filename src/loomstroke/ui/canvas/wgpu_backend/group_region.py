"""Conservative raster group bounds and a temporary region-sized GPU view."""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Any

from .types import WgpuViewParams


def group_target_region(compositor: Any, scene: Any, children: tuple[Any, ...]):
    """Return integer bounds in the parent target, or its full extent if unknown.

    Effects and direct vector/3D draws retain the established full target contract.
    Masks can only reduce coverage, so ignoring their bounds is conservative.
    """
    width, height = compositor._composite_size(scene)
    full = (0, 0, width, height)
    affine = compositor._view.affine if compositor._view is not None else (1, 0, 0, 1, 0, 0)
    points: list[tuple[float, float]] = []

    def visit(layer: Any, depth: int = 0) -> bool:
        if depth >= compositor.MAX_WGPU_GROUP_DEPTH or getattr(layer, "vector_direct_ready", False):
            return False
        if not getattr(layer, "visible", True) or float(getattr(layer, "opacity", 1.0)) <= 0:
            return True
        kind = getattr(layer, "layer_type", "raster")
        nested = tuple(getattr(layer, "children", ()) or ())
        if kind == "group":
            return all(visit(child, depth + 1) for child in nested)
        if nested or kind not in {"raster", "text", "vector", "ai_effect"}:
            return False
        image = getattr(layer, "image", None)
        if image is None or image.isNull():
            return not getattr(layer, "vector_objects", ())
        scale = max(1, int(getattr(layer, "vector_raster_scale", 1) or 1))
        origin = tuple(getattr(layer, "vector_raster_origin", (0, 0)) or (0, 0))
        region = compositor.transform_processor.compute_draw_region(
            layer,
            canvas_width=scene.width,
            canvas_height=scene.height,
            texture_width=-(-image.width() // scale),
            texture_height=-(-image.height() // scale),
            extra_offset=(int(origin[0]), int(origin[1])),
        )
        if region is not None:
            x, y = region.dest_origin
            w, h = region.dest_size
            a, b, c, d, tx, ty = affine
            points.extend(
                (a * px + b * py + tx, c * px + d * py + ty)
                for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
            )
        return True

    if not all(visit(child) for child in children) or not points:
        return full
    if not all(math.isfinite(value) for point in points for value in point):
        return full
    # Include sampling/antialiasing boundaries and round only in physical pixels.
    x0 = max(0, math.floor(min(x for x, _ in points)) - 2)
    y0 = max(0, math.floor(min(y for _, y in points)) - 2)
    x1 = min(width, math.ceil(max(x for x, _ in points)) + 2)
    y1 = min(height, math.ceil(max(y for _, y in points)) + 2)
    if x1 <= x0 or y1 <= y0:
        return full
    return x0, y0, x1 - x0, y1 - y0


@contextmanager
def group_region_view(compositor: Any, region: tuple[int, int, int, int], scene: Any):
    """Map the same canvas pixels into a cropped target and restore the parent."""
    previous, inverse = compositor._view, compositor._view_inverse
    x, y, width, height = region
    if previous is None and region == (0, 0, scene.width, scene.height):
        yield
        return
    affine = previous.affine if previous is not None else (1, 0, 0, 1, 0, 0)
    a, b, c, d, tx, ty = affine
    view = WgpuViewParams((a, b, c, d, tx - x, ty - y), width, height)
    compositor._view, compositor._view_inverse = view, view.inverse()
    try:
        yield
    finally:
        compositor._view, compositor._view_inverse = previous, inverse
