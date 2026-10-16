"""Pan/reuse geometry helpers for screen-space vector overlay."""

from __future__ import annotations

import math
from typing import Any


def normalize_raster_padding(
    raster_padding: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    try:
        px = float(raster_padding[0])
        py = float(raster_padding[1])
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0)
    if not math.isfinite(px) or px < 0.0:
        px = 0.0
    if not math.isfinite(py) or py < 0.0:
        py = 0.0
    return (px, py)


def normalize_output_offset(
    output_offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    try:
        ox = float(output_offset[0])
        oy = float(output_offset[1])
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0)
    if not math.isfinite(ox) or ox < 0.0:
        ox = 0.0
    if not math.isfinite(oy) or oy < 0.0:
        oy = 0.0
    return (ox, oy)


def edge_overscan_logical(
    state: dict[str, Any],
    *,
    min_edge: int,
    max_edge: int,
    base: float,
    zoom_factor: float,
) -> int:
    try:
        zoom = abs(float(state.get("zoom", 1.0)))
    except (TypeError, ValueError):
        zoom = 1.0
    if not math.isfinite(zoom):
        zoom = 1.0
    overscan = math.ceil(base + (zoom_factor * zoom))
    return max(min_edge, min(max_edge, overscan))


def overlay_raster_geometry(
    state: dict[str, Any],
    *,
    widget_w_logical: int,
    widget_h_logical: int,
    output_offset: tuple[float, float] = (0.0, 0.0),
    min_edge: int,
    max_edge: int,
    base: float,
    zoom_factor: float,
) -> tuple[int, int, int, int]:
    ox, oy = normalize_output_offset(output_offset)
    overscan = edge_overscan_logical(
        state,
        min_edge=min_edge,
        max_edge=max_edge,
        base=base,
        zoom_factor=zoom_factor,
    )
    pad_x = overscan + math.ceil(ox)
    pad_y = overscan + math.ceil(oy)
    return (
        pad_x,
        pad_y,
        max(1, widget_w_logical + pad_x + overscan),
        max(1, widget_h_logical + pad_y + overscan),
    )


def pan_delta_logical(
    prev_state: dict[str, Any],
    curr_state: dict[str, Any],
) -> tuple[float, float]:
    dx_cs = prev_state.get("center_x", 0.0) - curr_state.get("center_x", 0.0)
    dy_cs = prev_state.get("center_y", 0.0) - curr_state.get("center_y", 0.0)
    zoom = float(curr_state.get("zoom", 1.0))
    rot_deg = float(curr_state.get("rotation", 0.0))
    if rot_deg == 0.0:
        return dx_cs * zoom, dy_cs * zoom
    rad = math.radians(rot_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    return (
        (dx_cs * cos_r - dy_cs * sin_r) * zoom,
        (dx_cs * sin_r + dy_cs * cos_r) * zoom,
    )


def entry_covers_pan_delta(
    entry: Any,
    dx: float,
    dy: float,
    *,
    widget_w_logical: int,
    widget_h_logical: int,
    raster_padding: tuple[float, float] = (0.0, 0.0),
) -> bool:
    """Return True if a pan-reused overlay image still covers the widget."""

    if not math.isfinite(float(dx)) or not math.isfinite(float(dy)):
        return False
    px, py = normalize_raster_padding(raster_padding)
    try:
        dpr = float(entry.image.devicePixelRatio())
    except (TypeError, ValueError):
        dpr = 1.0
    if not math.isfinite(dpr) or dpr <= 0.0:
        dpr = 1.0

    logical_w = float(entry.image.width()) / dpr
    logical_h = float(entry.image.height()) / dpr
    if logical_w <= 0.0 or logical_h <= 0.0:
        return False

    right_margin = max(0.0, logical_w - float(widget_w_logical) - px)
    bottom_margin = max(0.0, logical_h - float(widget_h_logical) - py)
    return -right_margin <= float(dx) <= px and -bottom_margin <= float(dy) <= py
