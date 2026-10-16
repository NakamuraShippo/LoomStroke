"""Bake a vector gap into an explicit pixel conversion, retaining derived windows."""

import numpy as np

from loomstroke.core.comic_panels.convex_edges import edge_ownership_masks, span_ownership_mask
from loomstroke.core.comic_panels.edge_erasure import erased_edge_mask
from loomstroke.core.comic_panels.edge_span_mask import span_window
from loomstroke.core.comic_panels.membership import PART_KEY

from .panel_convex_plan import boundary_depths
from .panel_corner_ownership import retained_corner_owners


def bake_vector_erasure(state, panel_id, edge, index, image, origin, endpoints, metadata):
    marker = metadata[PART_KEY]
    if not marker.get("erase_strokes"):
        return image, state.panel_parts[edge.part_layer_id].values["layer_mask"]
    shifted = tuple((p[0] - origin[0], p[1] - origin[1]) for p in endpoints)
    coverage = erased_edge_mask(None, metadata, shifted, (image.width(), image.height()))
    alpha = np.frombuffer(coverage.constBits(), np.uint8).reshape(
        image.height(), coverage.bytesPerLine()
    )[:, : image.width()]
    rows = (
        np.frombuffer(image.bits(), np.uint8)
        .reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4]
        .reshape(image.height(), image.width(), 4)
    )
    rows[:] = ((rows.astype(np.uint16) * alpha[:, :, None].astype(np.uint16) + 127) // 255).astype(
        np.uint8
    )
    marker.pop("erase_strokes")
    saved_mask = state.panel_parts[edge.part_layer_id].values["layer_mask"]
    page_size = (saved_mask.width(), saved_mask.height())
    polygon = state.panel_polygons.get(panel_id)
    if polygon:
        owners = retained_corner_owners(state, panel_id, polygon, boundary_depths(state.layout))
        position = polygon.boundaries.index(edge.boundary_ref)
        style = state.panel_sources[panel_id][1].style
        mask = edge_ownership_masks(
            page_size,
            polygon.points,
            owners,
            corner_radius=style.stroke_width * style.miter_limit / 2 + 2,
        )[position]
        return image, span_ownership_mask(
            mask,
            polygon.points[position],
            polygon.points[(position + 1) % len(polygon.points)],
            edge.span,
            window_ends=marker.get("window_ends", (False, False)),
        )
    if marker.get("span_window"):
        return image, span_window(
            page_size,
            state.panel_rects[panel_id],
            edge.side or ("top", "right", "bottom", "left")[index],
            edge.span,
            window_ends=marker.get("window_ends", (False, False)),
            width=marker.get("window_width", 0),
        )
    return image, None
