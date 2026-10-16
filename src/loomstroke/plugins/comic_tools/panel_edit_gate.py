"""Validate detached frame edit targets and their ordinary parent groups."""

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError


def require_editable_parts(state, layer_ids):
    for target_id in layer_ids:
        layer_id = target_id
        seen = set()
        while layer_id:
            part = state.panel_parts.get(layer_id)
            if layer_id in seen or part is None:
                raise PanelFrameError("frame edit has missing or cyclic ancestry")
            seen.add(layer_id)
            values = part.values
            if values["locked"] or values["mask_edit_mode"]:
                raise PanelFrameError(
                    "frame edit target or ancestor is locked or has an unfinished mask edit"
                )
            if values.get("_preview_transform") is not None:
                raise PanelFrameError("frame edit target has an unfinished transform")
            layer_id = part.parent_id


def require_panel_editable(state, panel_id, *, edges=(), geometry=False):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    if frame is None:
        raise PanelFrameError("frame edit requires a materialized panel")
    targets = [frame.panel_group_id]
    if geometry:
        targets.append(frame.content_group_id)
        edges = frame.edges
    targets.extend(edge.part_layer_id for edge in edges)
    require_editable_parts(state, targets)
