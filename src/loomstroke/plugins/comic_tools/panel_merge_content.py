"""Retain independent content compositing when adjacent panels are merged."""

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.clip_mask import union_clip_mask
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.command_types import BlendMode
from loomstroke.core.commands_layer_tree import ReparentLayerCommand

from .panel_clip_policy import authored_clip


def merge_content_bundle(state, panel_ids, frames, rect, page_size):
    groups = [state.panel_parts[frames[pid].content_group_id] for pid in panel_ids]
    authored = [authored_clip(state, pid, page_size) for pid in panel_ids]
    needs_bundle = any(authored) or any(
        g.values["opacity"] != 1 or g.values["blend_mode"] != BlendMode.NORMAL for g in groups
    )
    if not needs_bundle:
        return None
    primary = frames[panel_ids[0]]
    group_id = f"panel-content-{uuid.uuid4().hex}"

    def depth(part):
        return int(part.values["layer_type"] == "group") + max(
            (depth(state.panel_parts[c]) for c in part.children), default=0
        )

    parent = state.panel_parts[primary.panel_group_id]
    ancestors = 0
    while parent:
        ancestors += int(parent.values["layer_type"] == "group")
        parent = state.panel_parts.get(parent.parent_id)
    if ancestors + 1 + max(depth(g) for g in groups) > 8:
        raise PanelFrameError("prepared part exceeds compositor group depth")
    x, y, w, h = rect
    inset = primary.clip_inset_px
    commands = [
        PanelLayerInsertCommand(
            group_id,
            parent_id=primary.panel_group_id,
            position=0,
            attributes={
                "name": "Content",
                "layer_type": "group",
                "metadata": {PART_KEY: {"version": 1, "role": "content"}},
                "layer_mask": union_clip_mask(
                    tuple(map(int, page_size)),
                    [(x + inset, y + inset, w - 2 * inset, h - 2 * inset)],
                ),
            },
        )
    ]
    for group, preserve in zip(groups, authored, strict=True):
        metadata = copy.deepcopy(group.values["metadata"])
        metadata.setdefault(PART_KEY, {})["role"] = "content_bundle"
        updates = {"metadata": metadata}
        if not preserve:
            updates["layer_mask"] = None
        commands.append(
            PanelLayerPatchCommand(
                group.layer_id, before={key: group.values[key] for key in updates}, after=updates
            )
        )
        commands.append(ReparentLayerCommand(group.layer_id, group_id))
    return replace(primary, content_group_id=group_id), commands
