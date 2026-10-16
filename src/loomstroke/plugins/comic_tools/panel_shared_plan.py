"""One page-fixed shared source, union clip, and explicit view independence."""

from __future__ import annotations

import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.clip_mask import union_clip_mask
from loomstroke.core.comic_panels.content_copy_command import PanelContentCopyCommand
from loomstroke.core.comic_panels.convex_clip import inset_polygon, polygon_clip_mask
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError, SharedContent
from loomstroke.core.comic_panels.shared_background_command import SharedBackgroundInsertCommand
from loomstroke.core.comic_panels.shared_metadata import read_shared_content, write_shared_content
from loomstroke.core.commands_layer_tree import ReparentLayerCommand

from .panel_source_guard import source_guard


def _shared_mask(state, panel_ids, page_size):
    rects, polygons = [], []
    for panel_id in panel_ids:
        record = state.panel_records.get(panel_id)
        frame = read_frame_layer(record.metadata) if record else None
        if frame is None:
            raise PanelFrameError("shared display requires materialized targets")
        if not _owner_visible(state, frame.panel_group_id):
            continue
        x, y, w, h = state.panel_rects[panel_id]
        inset = frame.clip_inset_px
        if state.panel_polygons:
            polygons.append(inset_polygon(state.panel_polygons[panel_id].points, inset))
        rects.append((x + inset, y + inset, w - 2 * inset, h - 2 * inset))
    if polygons:
        return polygon_clip_mask(page_size, polygons)
    return union_clip_mask(page_size, rects)


def _owner_visible(state, group_id):
    part = state.panel_parts.get(group_id)
    # Fresh, detached template slots have not been inserted yet and are visible.
    if part is None:
        return True
    seen = set()
    while part is not None:
        if part.layer_id in seen:
            raise PanelFrameError("shared display owner has cyclic ancestry")
        seen.add(part.layer_id)
        if not part.values["visible"]:
            return False
        if not part.parent_id:
            return True
        part = state.panel_parts.get(part.parent_id)
        if part is None:
            raise PanelFrameError("shared display owner has missing ancestry")
    return False


def share_content_commands(state, source_id, *, panel_ids, page_id, page_size):
    if len(panel_ids) < 2 or len(set(panel_ids)) != len(panel_ids):
        raise PanelFrameError("common display requires distinct targets")
    group_id = f"panel-shared-{uuid.uuid4().hex}"
    shared = SharedContent(page_id, group_id, tuple(panel_ids))
    group = SharedBackgroundInsertCommand(
        group_id,
        anchor_ids=[
            read_frame_layer(state.panel_records[pid].metadata).panel_group_id for pid in panel_ids
        ],
        attributes={
            "name": "Shared panel content",
            "layer_type": "group",
            "metadata": write_shared_content({}, shared),
            "layer_mask": _shared_mask(state, panel_ids, page_size),
        },
    )
    return [group, ReparentLayerCommand(source_id, group_id)], shared


def independent_shared_view_command(state, group_id, panel_id, *, page_id, page_size):
    part = state.panel_parts[group_id]
    shared = read_shared_content(part.values["metadata"], group_id=group_id)
    if shared is None or shared.page_id != page_id or panel_id not in shared.panel_ids:
        raise PanelFrameError("shared source or display reference is missing")
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    if len(shared.panel_ids) < 2:
        # Explicit independence of the last view moves the original source.
        # It neither deletes a source nor leaves a dangling shared reference.
        metadata = dict(part.values["metadata"])
        metadata.pop("loomstroke.comictools.shared_content")
        return PanelEditCommand(
            [
                source_guard(state),
                PanelLayerPatchCommand(
                    group_id,
                    before={
                        "metadata": part.values["metadata"],
                        "layer_mask": part.values["layer_mask"],
                    },
                    after={"metadata": metadata, "layer_mask": None},
                ),
                ReparentLayerCommand(group_id, frame.content_group_id),
            ],
            label="Make last shared view independent",
        )
    targets = tuple(p for p in shared.panel_ids if p != panel_id)
    duplicate = PanelContentCopyCommand(
        group_id,
        frame.content_group_id,
        expected_version=part.texture_version,
        expected_parent_id=part.parent_id,
        remove_shared_display=True,
    )
    metadata = write_shared_content(part.values["metadata"], replace(shared, panel_ids=targets))
    update = PanelLayerPatchCommand(
        group_id,
        before={"metadata": part.values["metadata"], "layer_mask": part.values["layer_mask"]},
        after={"metadata": metadata, "layer_mask": _shared_mask(state, targets, page_size)},
    )
    return PanelEditCommand(
        [source_guard(state), duplicate, update], label="Make shared view independent"
    )
