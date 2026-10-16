"""Create dialogue, balloon ink and ownership as a single prepared edit."""

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.membership import UNIT_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand

from .panel_source_guard import source_guard


def create_panel_balloon_command(state, record, body, text, *, guarded=True):
    frame = read_frame_layer(state.panel_records[record.panel_id].metadata)
    if frame is None:
        raise PanelFrameError("balloon target is not a frame layer")
    unit_id = f"balloon-unit-{uuid.uuid4().hex}"
    commands = [
        PanelLayerInsertCommand(
            unit_id,
            parent_id=frame.content_group_id,
            attributes={
                "name": "Balloon",
                "layer_type": "group",
                "metadata": {UNIT_KEY: {"version": 1, "balloon_id": record.balloon_id}},
            },
        ),
    ]
    if guarded:
        commands.insert(0, source_guard(state))
    for kind, obj in (("body", body), ("text", text)):
        layer_id = f"{unit_id}-{kind}"
        meta = copy.deepcopy(obj.meta)
        meta["layer_id"] = layer_id
        commands.append(
            PanelLayerInsertCommand(
                layer_id,
                parent_id=unit_id,
                attributes={
                    "name": kind,
                    "layer_type": "vector",
                    "vector_kind": "text" if kind == "text" else "draw",
                    "vector_objects": [replace(obj, meta=meta)],
                },
            )
        )
    return PanelEditCommand(commands, label="Create panel balloon and dialogue")
