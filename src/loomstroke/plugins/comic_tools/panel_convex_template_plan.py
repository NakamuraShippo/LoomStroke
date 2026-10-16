"""Prepare fresh template slots without reshaping retained processed frames."""

import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import BoundaryRef

from .balloon_objects import build_panel_object
from .commands import ComicEditCommand
from .model import PanelRecord
from .panel_frame_conversion import prepare_panel_frame_conversion
from .panel_frame_layout_adapter import _boundaries


def convex_template_slots(state, layout, polygons, *, page_size):
    references = []
    _boundaries(
        layout.root,
        {side: BoundaryRef("page", side) for side in ("top", "right", "bottom", "left")},
        references,
    )
    references = {(pid, side): ref for pid, side, ref in references}
    fresh = [pid for pid in polygons if pid not in state.panel_records]
    additions, conversions, opacity_commands = [], [], []
    style = next(iter(state.panel_sources.values()))[1].style
    for pid in fresh:
        record = PanelRecord(pid, gutter_px=layout.gutter_px)
        obj = replace(
            build_panel_object(record, polygons[pid].rect),
            style=style,
            id=f"comic-panel-{uuid.uuid4().hex}",
        )
        obj.meta["layer_id"] = state.frames_layer_id
        additions.append((state.frames_layer_id, obj))
        conversion = prepare_panel_frame_conversion(
            source_layer_id=state.frames_layer_id,
            source_object=obj,
            record=record,
            rect=polygons[pid].rect,
            boundaries={side: references[pid, side] for side in ("top", "right", "bottom", "left")},
            page_size=tuple(map(int, page_size)),
            max_group_depth=8,
        )
        conversions.append(conversion)
        first = next(iter(state.panel_records.values()))
        source_edge = read_frame_layer(first.metadata).edges[0]
        opacity = state.panel_parts[source_edge.part_layer_id].values["opacity"]
        if opacity != 1:
            opacity_commands.extend(
                PanelLayerPatchCommand(
                    edge.part_layer_id, before={"opacity": 1.0}, after={"opacity": opacity}
                )
                for edge in conversion._frame.edges
            )
    return PanelEditCommand(
        [
            ComicEditCommand(label="Create template slots", additions=additions),
            *conversions,
            *opacity_commands,
        ],
        label="Create template slots",
    )
