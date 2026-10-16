"""Direct-rendering eligibility for vector layers (spec Phase V1-V4).

Routes a vector layer to ``WgpuVectorRenderer`` only when every property the
direct path does not implement yet is absent, so pixel output stays close to
the compatibility raster path.  Anything else keeps using the existing
QImage raster snapshot.  Later phases widen these checks instead of
scattering feature tests through the renderer.

V4 scope: draw/shape kinds, fill + stroke (width/join/cap/miter/dash), open
and closed paths, the layer's non-destructive affine transform, any opacity,
every compositor-enabled blend mode, the layer pixel mask, and clipping
(mask/clip/opacity/blend all apply in the shared blend pass, exactly like
raster layers).

V5 (stage 1) adds text objects whose TextLayoutAdapter snapshot resolved
(``obj.text`` is set — horizontal glyph runs); unsupported text features
leave ``obj.text`` as None and keep the layer on the raster path.  Filter
children still force the compositor's full-frame flatten regardless of
layer type (the scene-level guard in ``render_scene`` handles that case).
"""

from __future__ import annotations

import math

from loomstroke.core.vector_render_models import (
    IDENTITY_TRANSFORM,
    VectorObjectSnapshot,
    VectorRenderLayerSnapshot,
)
from loomstroke.ui.canvas.wgpu_backend.shader_library import WGPU_ENABLED_BLEND_MODES

_DIRECT_KINDS = frozenset({"draw", "shape"})
_DIRECT_LAYER_KINDS = frozenset({"draw", "shape", "text"})
# Matches _MAX_VECTOR_COORD_ABS in plugins.vector_tools.common.
_MAX_COORD_ABS = 1_000_000.0


def is_direct_fill_eligible_layer(layer: VectorRenderLayerSnapshot) -> bool:
    """Return whether *layer* can be drawn by the direct vector path."""

    if layer.vector_kind not in _DIRECT_LAYER_KINDS or not layer.objects:
        return False
    blocked = (
        layer.blend_mode not in WGPU_ENABLED_BLEND_MODES
        or any(ref.visible for ref in layer.filter_refs)
        or not all(math.isfinite(value) for value in layer.layer_transform)
    )
    if blocked:
        return False
    return all(is_direct_fill_eligible_object(obj) for obj in layer.objects)


def is_direct_fill_eligible_object(obj: VectorObjectSnapshot) -> bool:  # noqa: PLR0911
    """Return whether *obj* is an object the direct path can draw."""

    if obj.is_preview:
        return False
    if obj.has_variable_width:
        # CT-15: variable width renders as an outline on the raster path.
        return False
    if obj.object_kind == "text":
        # V5: eligible only when the TextLayoutAdapter resolved every
        # feature the object uses into glyph runs (spec §12.5 fallback).
        return obj.text is not None
    if obj.object_kind not in _DIRECT_KINDS:
        return False
    if not obj.paths:
        return False
    if obj.transform != IDENTITY_TRANSFORM:
        return False
    style = obj.style
    if not math.isfinite(style.stroke_width) or not math.isfinite(style.miter_limit):
        return False
    if not all(math.isfinite(value) for value in style.dash):
        return False
    # Malformed coordinates fall back per object at compile time; checking
    # here keeps the whole layer on the raster path instead of dropping the
    # object silently.
    return all(_path_coordinates_valid(path) for path in obj.paths)


def _path_coordinates_valid(path: object) -> bool:
    for anchor in getattr(path, "anchors", ()):
        for point in (anchor.pos, anchor.h_in, anchor.h_out):
            if point is None:
                continue
            if not (
                math.isfinite(point[0])
                and math.isfinite(point[1])
                and abs(point[0]) <= _MAX_COORD_ABS
                and abs(point[1]) <= _MAX_COORD_ABS
            ):
                return False
    return True
