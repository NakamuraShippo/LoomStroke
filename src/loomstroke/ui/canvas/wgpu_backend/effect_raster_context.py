"""Compatibility imports for the canonical filter module."""

from .filter_raster_context import (
    DEFAULT_FILTER_RASTER_CONTEXT,
    FilterRasterContext,
)

EffectRasterContext = FilterRasterContext
DEFAULT_EFFECT_RASTER_CONTEXT = DEFAULT_FILTER_RASTER_CONTEXT

__all__ = ["DEFAULT_EFFECT_RASTER_CONTEXT", "EffectRasterContext"]
