"""Explicit clip/overflow/page-front changes preserve whole content units."""

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import BALLOON_KEY, FRONT_KEY, PART_KEY, TEXT_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.commands_layer_tree import ReparentLayerCommand

from .panel_source_guard import source_guard


def content_owner_commands(state, item_id, panel_id, parent_id, *, panel_group_id=None):
    """Front units keep their z-plane; other units follow their clip policy."""
    part = state.panel_parts[item_id]
    front = (part.values["metadata"] or {}).get(FRONT_KEY)
    if not front:
        old_parent = state.panel_parts.get(part.parent_id)
        if (
            old_parent
            and (old_parent.values["metadata"] or {}).get(PART_KEY, {}).get("role") == "overflow"
        ):
            if panel_group_id is None:
                panel_group_id = read_frame_layer(
                    state.panel_records[panel_id].metadata
                ).panel_group_id
            group_id = next(
                (
                    key
                    for key, value in state.panel_parts.items()
                    if value.parent_id == panel_group_id
                    and (value.values["metadata"] or {}).get(PART_KEY, {}).get("role") == "overflow"
                ),
                None,
            )
            commands = []
            if group_id is None:
                group_id = f"panel-overflow-{uuid.uuid4().hex}"
                commands.append(
                    PanelLayerInsertCommand(
                        group_id,
                        parent_id=panel_group_id,
                        attributes={
                            "name": "Overflow",
                            "layer_type": "group",
                            "metadata": {PART_KEY: {"version": 1, "role": "overflow"}},
                        },
                    )
                )
            commands.append(ReparentLayerCommand(item_id, group_id))
            return commands
        return [ReparentLayerCommand(item_id, parent_id)]
    commands = []
    metadata = copy.deepcopy(part.values["metadata"])
    metadata[FRONT_KEY]["panel_id"] = panel_id
    commands.append(
        PanelLayerPatchCommand(
            item_id, before={"metadata": part.values["metadata"]}, after={"metadata": metadata}
        )
    )

    def visit(node):
        objects = []
        for obj in node.values["vector_objects"]:
            meta = copy.deepcopy(obj.meta)
            for key in (BALLOON_KEY, TEXT_KEY):
                if key in meta:
                    meta[key]["panel_id"] = panel_id
            objects.append(replace(obj, meta=meta))
        if objects != node.values["vector_objects"]:
            commands.append(
                PanelLayerPatchCommand(
                    node.layer_id,
                    before={"vector_objects": node.values["vector_objects"]},
                    after={"vector_objects": objects},
                )
            )
        for child in node.children:
            visit(state.panel_parts[child])

    visit(part)
    return commands


def display_content_command(  # noqa: PLR0912 - explicit display/owner transitions
    state, item_id, panel_id, policy
):
    if policy not in {"clip", "overflow", "front"}:
        raise PanelFrameError("unsupported panel content display")
    record = state.panel_records.get(panel_id)
    frame = read_frame_layer(record.metadata) if record else None
    if frame is None:
        raise PanelFrameError("display requires a materialized panel")
    units = state.panel_contents.get(frame.content_group_id, ())
    if item_id not in {unit.layer_id for unit in units}:
        raise PanelFrameError("select a whole content unit in this panel")
    part = state.panel_parts[item_id]
    group = state.panel_parts[frame.panel_group_id]
    if part.values["locked"] or group.values["locked"] or not group.values["visible"]:
        raise PanelFrameError("content or owning panel is hidden or locked")
    commands = [source_guard(state)]
    if policy == "clip":
        parent_id = frame.content_group_id
    else:
        role = "page_front" if policy == "front" else "overflow"
        parent_id = next(
            (
                key
                for key, value in state.panel_parts.items()
                if (value.values["metadata"] or {}).get(PART_KEY, {}).get("role") == role
                and value.parent_id == (None if policy == "front" else frame.panel_group_id)
            ),
            None,
        )
        if parent_id is None:
            parent_id = f"panel-{role}-{uuid.uuid4().hex}"
            commands.append(
                PanelLayerInsertCommand(
                    parent_id,
                    parent_id=None if policy == "front" else frame.panel_group_id,
                    attributes={
                        "name": "Page front" if policy == "front" else "Overflow",
                        "layer_type": "group",
                        "metadata": {PART_KEY: {"version": 1, "role": role}},
                    },
                )
            )
    if part.parent_id == parent_id:
        raise PanelFrameError("content already uses this display policy")
    commands.append(ReparentLayerCommand(item_id, parent_id))
    if policy == "front":
        metadata = copy.deepcopy(part.values["metadata"] or {})
        metadata[FRONT_KEY] = {"version": 1, "panel_id": panel_id}
        commands.append(
            PanelLayerPatchCommand(
                item_id, before={"metadata": part.values["metadata"]}, after={"metadata": metadata}
            )
        )
        # Reparenting to a root group clears ancestry. Restore the explicit
        # owner on body/text records together with the front marker.
        # Build edits from the post-reparent object metadata, so the guarded
        # patches match the actual command boundary rather than the old owner.
        for node_id in _unit_nodes(state, item_id):
            node = state.panel_parts[node_id]
            before, after = [], []
            for obj in node.values["vector_objects"]:
                empty, owned = copy.deepcopy(obj.meta), copy.deepcopy(obj.meta)
                for key in (BALLOON_KEY, TEXT_KEY):
                    if key in empty:
                        empty[key]["panel_id"] = ""
                        owned[key]["panel_id"] = panel_id
                before.append(replace(obj, meta=empty))
                after.append(replace(obj, meta=owned))
            if before != after:
                commands.append(
                    PanelLayerPatchCommand(
                        node_id, before={"vector_objects": before}, after={"vector_objects": after}
                    )
                )
    return PanelEditCommand(commands, label="Change panel content display")


def _unit_nodes(state, item_id):
    yield item_id
    for child in state.panel_parts[item_id].children:
        yield from _unit_nodes(state, child)
