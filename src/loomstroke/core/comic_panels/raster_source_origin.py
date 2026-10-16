"""Track image padding in the edge's fixed source coordinate system."""

from __future__ import annotations

import copy
from dataclasses import replace

from loomstroke.core.command_types import LayerTransform

from .membership import PART_KEY
from .models import PanelFrameError


def raster_image_anchor(anchor, metadata, transform, offset):
    """Image expansion moves its array origin, while the registered anchor stays fixed.

    A paint basis is established only with an identity layer transform. Ordinary
    brush history may retain transparent padding after Undo; infer that padding
    from existing layer offsets instead of mutating carrier metadata per segment.
    """
    result = copy.deepcopy(metadata or {})
    marker = result.setdefault(PART_KEY, {})
    origin = marker.get("source_image_origin", [0.0, 0.0])
    if len(origin) != 2:
        raise PanelFrameError("invalid raster source origin")
    basis = marker.get("paint_basis_origin")
    if basis is not None:
        if len(basis) != 2 or transform != LayerTransform.identity():
            raise PanelFrameError("raster edge paint basis is inconsistent")
        origin = [origin[0] + offset[0] - basis[0], origin[1] + offset[1] - basis[1]]
    marker["source_image_origin"] = list(origin)
    marker.pop("paint_basis_origin", None)
    effective = replace(
        anchor,
        source_xy_px=(anchor.source_xy_px[0] - origin[0], anchor.source_xy_px[1] - origin[1]),
    )
    return effective, result
