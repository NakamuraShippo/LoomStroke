"""Read grid settings without Qt or plugin dependencies."""

from __future__ import annotations

from typing import Any

from .guide_layout_geometry import unit_to_pixels
from .layout_grid_model import GRID_KINDS, LayoutGrid, Point


def read_layout_grid(
    settings: Any, *, size: float = 32, origin: Point = (0.0, 0.0), dpi: Point = (72.0, 72.0)
) -> LayoutGrid:
    if settings is None:
        return LayoutGrid(spacing=max(1, size))
    kind = settings.get("view/grid_kind", "square")
    kind = kind if kind in GRID_KINDS else "square"
    try:
        baseline_spacing, baseline_start = 20.0, 0.0
        if kind == "baseline":
            unit = settings.get("view/grid_baseline_unit", "px")
            baseline_spacing = unit_to_pixels(
                float(settings.get("view/grid_baseline_spacing", 20.0)), unit, dpi[1]
            )
            baseline_start = unit_to_pixels(
                float(settings.get("view/grid_baseline_start", 0.0)), unit, dpi[1]
            )
        return LayoutGrid(
            kind=kind,
            baseline_spacing=baseline_spacing,
            baseline_start=baseline_start,
            spacing=max(1, size),
            y_spacing=max(1, float(settings.get("view/grid_y_size", 32))),
            major_every=max(1, min(1000, int(settings.get("view/grid_major_every", 5)))),
            origin=origin if settings.get("view/grid_follow_ruler_origin", False) else (0.0, 0.0),
            rotation=float(settings.get("view/grid_rotation", 0.0)),
            polar_divisions=max(3, min(360, int(settings.get("view/grid_polar_divisions", 12)))),
        )
    except (TypeError, ValueError, OverflowError):
        return LayoutGrid(spacing=max(1, size))
