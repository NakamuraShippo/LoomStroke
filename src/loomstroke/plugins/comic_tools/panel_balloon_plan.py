"""Attach existing balloon body, text and raster rig as one allocation unit."""

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import BALLOON_KEY, TEXT_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.commands_layer_tree import ReparentLayerCommand

from .balloon_part_move_command import BalloonPartMoveCommand
from .panel_source_guard import source_guard
from .raster_parts import RASTER_PART_NAMESPACE

UNIT_KEY = "loomstroke.comictools.balloon_unit"


def _object_for_unit(obj, layer_id, panel_id):
    story = (obj.meta.get("text_tool") or {}).get("linked_story")
    if isinstance(story, dict) and story.get("scope") == "document":
        raise PanelFrameError("document-linked balloon grouping requires link relocation")
    meta = copy.deepcopy(obj.meta)
    for key in (BALLOON_KEY, TEXT_KEY):
        if key in meta:
            meta[key]["panel_id"] = panel_id
    meta["layer_id"] = layer_id
    return replace(obj, meta=meta)


def attach_balloon_command(  # noqa: PLR0912 - whole-unit source checks
    state, balloon_id, panel_id, *, standalone=False
):
    record = state.balloons.get(balloon_id)
    target = state.panel_records.get(panel_id)
    frame = read_frame_layer(target.metadata) if target else None
    if record is None or (frame is None and not standalone):
        raise PanelFrameError("balloon and materialized panel are required")
    parent_id = frame.content_group_id if frame else None
    node = state.panel_parts.get(frame.panel_group_id) if frame else None
    seen = set()
    while node is not None:
        if node.layer_id in seen or node.values["locked"] or not node.values["visible"]:
            raise PanelFrameError("balloon target is hidden or locked")
        seen.add(node.layer_id)
        node = state.panel_parts.get(node.parent_id) if node.parent_id else None
    source_ids = {state.balloon_sources[balloon_id], state.text_sources[balloon_id]}
    if standalone:
        parents = {state.panel_parts[layer_id].parent_id for layer_id in source_ids}
        if len(parents) != 1:
            raise PanelFrameError("balloon sources must share a parent before grouping")
        parent_id = parents.pop()
    for layer_id in source_ids:
        part = state.panel_parts.get(layer_id)
        if (
            part is None
            or part.values["locked"]
            or part.values["transform"] != LayerTransform.identity()
        ):
            raise PanelFrameError("balloon source is missing, locked or transformed")
        if (
            part.values["clipping"]
            or part.values["effect_type"]
            or part.values["_preview_transform"]
        ):
            raise PanelFrameError("finish the balloon source clipping or filter edit first")
        parent = state.panel_parts.get(part.parent_id)
        if parent and UNIT_KEY in parent.values["metadata"]:
            return PanelEditCommand(
                [
                    source_guard(state),
                    ReparentLayerCommand(parent.layer_id, parent_id),
                ],
                label="Move whole balloon into panel",
            )
    token = uuid.uuid4().hex
    unit_id = f"balloon-unit-{token}"
    commands = [
        source_guard(state),
        PanelLayerInsertCommand(
            unit_id,
            parent_id=parent_id,
            attributes={
                "name": "Balloon",
                "layer_type": "group",
                "metadata": {UNIT_KEY: {"version": 1, "balloon_id": balloon_id}},
            },
        ),
    ]
    ordered_sources = sorted(
        source_ids, key=lambda value: (value == state.text_sources[balloon_id], value)
    )
    for index, source_id in enumerate(ordered_sources):
        source = state.panel_parts[source_id]
        selected, remaining = [], []
        for obj in source.values["vector_objects"]:
            payload = obj.meta.get(BALLOON_KEY) or obj.meta.get(TEXT_KEY)
            if payload and payload.get("balloon_id") == balloon_id:
                selected.append(
                    _object_for_unit(
                        obj, f"{unit_id}-{index}", record.panel_id if standalone else panel_id
                    )
                )
            else:
                remaining.append(obj)
        if not selected:
            raise PanelFrameError("balloon source objects are missing")
        commands.append(
            PanelLayerPatchCommand(
                source_id,
                before={"vector_objects": source.values["vector_objects"]},
                after={"vector_objects": remaining},
            )
        )
        commands.append(
            PanelLayerInsertCommand(
                f"{unit_id}-{index}",
                parent_id=unit_id,
                attributes={
                    "name": source_id,
                    "metadata": copy.deepcopy(source.values["metadata"]),
                    "layer_type": "vector",
                    "vector_kind": source.values["vector_kind"],
                    "vector_objects": selected,
                    "visible": source.values["visible"],
                    "opacity": source.values["opacity"],
                    "blend_mode": source.values["blend_mode"],
                    "offset_x": source.values["offset_x"],
                    "offset_y": source.values["offset_y"],
                    "layer_mask": source.values["layer_mask"],
                    "mask_enabled": source.values["mask_enabled"],
                },
            )
        )
    for part in state.panel_parts.values():
        marker = (part.values["metadata"].get("plugins") or {}).get(RASTER_PART_NAMESPACE, {})
        if marker.get("balloon_id") == balloon_id:
            commands.append(
                BalloonPartMoveCommand(
                    part.layer_id,
                    unit_id,
                    expected_parent_id=part.parent_id,
                    balloon_id=balloon_id,
                    target_position=1 if len(source_ids) > 1 else 0,
                )
            )
    return PanelEditCommand(commands, label="Attach whole balloon to panel")
