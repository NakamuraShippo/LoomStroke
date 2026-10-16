"""Canvas-pixel support needed around a vector layer's effect input raster."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .mosaic import mosaic_cell_size
from .noise_reduction import bilateral_denoise_params


def vector_raster_filter_padding(layer: Any) -> int:
    """Sum supports in the compositor's own-effect/visible-child-effect chain.

    Radii are logical canvas pixels. Raster prescaling and the GPU's kernel
    scaling must not multiply this margin a second time.
    """
    if not getattr(layer, "visible", True):
        return 0
    padding = _filter_support(layer)
    for child in getattr(layer, "children", ()) or ():
        if getattr(child, "layer_type", None) == "effect" and getattr(child, "visible", True):
            padding += _filter_support(child)
    return padding


def _finite_number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _filter_support(effect: Any) -> int:
    filter_type = str(getattr(effect, "filter_type", getattr(effect, "effect_type", "")) or "")
    raw_params = getattr(effect, "filter_params", getattr(effect, "effect_params", None))
    params = raw_params if isinstance(raw_params, Mapping) else {}
    if filter_type == "gaussian_blur":
        return max(0, min(250, int(_finite_number(params.get("radius"), 5.0))))
    if filter_type == "sharpen":
        radius = _finite_number(params.get("radius"), 1.0)
        amount = _finite_number(params.get("amount"), 50.0)
        if radius <= 0.0 or amount <= 0.0:
            return 0
        return min(250, max(1, int(radius)))
    if filter_type == "noise_reduction":
        radius, _, _ = bilateral_denoise_params(
            _finite_number(params.get("strength"), 10.0),
            _finite_number(params.get("color_strength"), 10.0),
        )
        return radius
    if filter_type == "mosaic":
        cell = mosaic_cell_size({"cell_size": _finite_number(params.get("cell_size"), 8.0)})
        return cell if cell > 1 else 0
    # Color-only filters and screentone have no spatial input neighbourhood.
    return 0


vector_raster_effect_padding = vector_raster_filter_padding
_effect_support = _filter_support
