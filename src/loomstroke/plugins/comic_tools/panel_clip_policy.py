"""Retain authored content coverage until a preview explicitly regenerates it."""

import copy

from loomstroke.core.comic_panels.clip_mask import union_clip_mask
from loomstroke.core.comic_panels.convex_clip import inset_polygon, polygon_clip_mask
from loomstroke.core.comic_panels.derived_clip import authored_coverage
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer


def authored_clip(state, panel_id, page_size):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    content = state.panel_parts[frame.content_group_id]
    values = content.values
    polygon = state.panel_polygons.get(panel_id)
    if polygon:
        expected = polygon_clip_mask(
            tuple(map(int, page_size)), [inset_polygon(polygon.points, frame.clip_inset_px)]
        )
    else:
        x, y, w, h = state.panel_rects[panel_id]
        inset = frame.clip_inset_px
        expected = union_clip_mask(
            tuple(map(int, page_size)), [(x + inset, y + inset, w - 2 * inset, h - 2 * inset)]
        )
    return authored_coverage(
        metadata=values["metadata"],
        enabled=values["mask_enabled"],
        mask=values["layer_mask"],
        frame=frame,
        expected=expected,
    )


def content_clip_command(state, panel_id, mask, page_size, *, regenerate=False):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    content = state.panel_parts[frame.content_group_id]
    metadata = copy.deepcopy(content.values["metadata"])
    marker = metadata.setdefault(PART_KEY, {})
    preserve = not regenerate and authored_clip(state, panel_id, page_size)
    marker["clip_auto_update"] = not preserve
    updates = {"metadata": metadata}
    if not preserve:
        updates.update(layer_mask=mask, mask_enabled=True)
    return PanelLayerPatchCommand(
        content.layer_id,
        before={k: content.values[k] for k in updates},
        after=updates,
    )
