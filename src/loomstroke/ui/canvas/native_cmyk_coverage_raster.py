"""Choose alpha-only rasters only for proven single-ink native vector geometry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loomstroke.core.japanese_text.story_preview import preview_objects
from loomstroke.core.native_cmyk.vector_paints import object_paints

from .native_cmyk_vector_masks import _single_paint


def native_coverage_rasterizer(
    canvas: Any, native: Any, fallback: Callable[..., Any]
) -> Callable[..., Any]:
    coverage = getattr(canvas, "_rasterize_vector_coverage_layer_for_render", None)
    profile = getattr(native, "profile", None)
    dtype = getattr(getattr(native, "inks", None), "dtype", None)
    extract_text = getattr(canvas, "_extract_text_payload", None)
    if not callable(coverage) or not callable(extract_text) or profile is None or dtype is None:
        return fallback

    def eligible(layer: Any) -> bool:
        preview = getattr(canvas, "_vector_preview", None)
        preview_layer = getattr(canvas, "_vector_preview_layer_id", None)
        objects = preview_objects(getattr(layer, "vector_objects", ()), preview)
        if preview is not None and preview_layer in (None, layer.id):
            objects = [obj for obj in objects if obj.id != preview.id] + [preview]
        if len(objects) != 1:
            return False
        obj = objects[0]
        # Qt's Alpha8 glyph rendering differs from ARGB32 at ordinary zoom.
        # Text, mixed paints and RGB-derived objects retain their RGBA raster.
        paints = object_paints(obj)
        if not paints or extract_text(obj) is not None:
            return False
        try:
            return _single_paint(obj, None, paints, profile, dtype) is not None
        except (AttributeError, TypeError, ValueError):
            # Unknown paint/style data keeps the original renderer's behavior.
            return False

    def rasterize(layer: Any, scale: int = 1) -> Any:
        render = coverage if eligible(layer) else fallback
        return render(layer, scale=scale) if scale != 1 else render(layer)

    return rasterize
