"""Atomic materialized/legacy rectangle layout changes.

Retained panel IDs keep their groups, content, and edge sources. New panels use
the same conversion command as explicit legacy conversion, inside one history.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import SHARED_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.shared_metadata import read_shared_content

from . import layout_tree as lt
from .balloon_objects import build_panel_object
from .commands import ComicEditCommand
from .model import PANEL_META_KEY, PanelRecord, merged_layer_metadata_with_layout
from .panel_frame_conversion import prepare_panel_frame_conversion
from .panel_frame_layout_adapter import layout_snapshot
from .panel_resize_plan import resize_panel_commands
from .panel_source_guard import source_guard


def materialized_layout_command(  # noqa: PLR0912, PLR0915 - ordered atomic layout plan
    state, new_layout, *, page_size, label, ownership=None, edge_policies=None, regenerate_clips=()
):
    leaves = lt.compute_panel_rects(new_layout, *page_size)
    removed = {
        pid
        for pid, r in state.panel_records.items()
        if not set(
            (ownership or {}).get(
                pid,
                (
                    read_frame_layer(r.metadata).layout_leaf_ids
                    if read_frame_layer(r.metadata)
                    else (pid,)
                ),
            )
        )
        <= leaves.keys()
    }
    if removed:
        raise PanelFrameError("removing populated panels requires an explicit content disposition")
    # Retained merged ownership can span multiple base leaves. Add only unowned leaves.
    records = dict(state.panel_records)
    owners = dict(ownership or {})
    owned = set()
    for panel_id, record in records.items():
        frame = read_frame_layer(record.metadata)
        owners.setdefault(panel_id, frame.layout_leaf_ids if frame else (panel_id,))
        owned.update(owners[panel_id])
        if frame and frame.layout_leaf_ids != tuple(owners[panel_id]):
            records[panel_id] = replace(
                record,
                metadata=write_frame_layer(
                    record.metadata, replace(frame, layout_leaf_ids=tuple(owners[panel_id]))
                ),
            )
    owned.update(leaf for ids in owners.values() for leaf in ids)
    fresh = (set(leaves) - owned) | (set(owners) - set(records))
    for panel_id in leaves:
        if panel_id in fresh:
            records[panel_id] = PanelRecord(panel_id, gutter_px=new_layout.gutter_px)
            owners.setdefault(panel_id, (panel_id,))
    for panel_id in fresh:
        records.setdefault(panel_id, PanelRecord(panel_id, gutter_px=new_layout.gutter_px))
    snapshot = layout_snapshot(new_layout, page_size, records, owners_override=owners)
    rects = {region.panel_id: region.rect for region in snapshot.regions}
    order = tuple(p for p in new_layout.reading_order if p in rects)
    order += tuple(p for p in rects if p not in order)
    new_layout = replace(new_layout, reading_order=order)
    records = {
        p: replace(r, reading_order=order.index(p), gutter_px=new_layout.gutter_px)
        for p, r in records.items()
    }
    boundaries = {(leaf, side): ref for leaf, side, ref in snapshot.boundaries}
    additions, replacements = [], []
    for panel_id, record in records.items():
        frame = read_frame_layer(record.metadata)
        if frame is not None:
            continue
        obj = build_panel_object(
            replace(record, reading_order=order.index(panel_id), gutter_px=new_layout.gutter_px),
            rects[panel_id],
        )
        if panel_id in fresh:
            obj = replace(obj, id=f"comic-panel-{uuid.uuid4().hex}")
            rx, ry, rw, rh = rects[panel_id]
            parent = next(
                (
                    pid
                    for pid, (x, y, w, h) in state.panel_rects.items()
                    if x <= rx and y <= ry and rx + rw <= x + w + 1e-6 and ry + rh <= y + h + 1e-6
                ),
                None,
            )
            if parent in state.panel_sources:
                obj = replace(obj, style=state.panel_sources[parent][1].style)
            obj.meta["layer_id"] = state.frames_layer_id
            additions.append((state.frames_layer_id, obj))
        else:
            source_id, old = state.panel_sources[panel_id]
            meta = copy.deepcopy(old.meta)
            payload = copy.deepcopy(meta[PANEL_META_KEY])
            payload.update(obj.meta[PANEL_META_KEY])
            meta[PANEL_META_KEY] = payload
            replacements.append((source_id, replace(old, paths=obj.paths, meta=meta)))
    commands = [
        source_guard(state, page_size=page_size),
        ComicEditCommand(
            label=label,
            additions=additions,
            replacements=replacements,
        ),
    ]
    commands.append(
        PanelLayerPatchCommand(
            state.frames_layer_id,
            before={"metadata": state.frames_layer_metadata},
            after={
                "metadata": merged_layer_metadata_with_layout(
                    state.frames_layer_metadata, lt.layout_to_payload(new_layout)
                )
            },
        )
    )
    for panel_id, record in state.panel_records.items():
        frame = read_frame_layer(record.metadata)
        if frame is None:
            continue
        refs = {
            side: _outer_ref(owners[panel_id], leaves, rects[panel_id], side, boundaries)
            for side in ("top", "right", "bottom", "left")
        }
        commands.extend(
            resize_panel_commands(
                state,
                panel_id,
                rects[panel_id],
                refs,
                page_size=page_size,
                policies=edge_policies,
                regenerate_clip=panel_id in regenerate_clips,
                record_updates={
                    "reading_order": order.index(panel_id),
                    "gutter_px": new_layout.gutter_px,
                },
            )
        )
        patch = commands[-1]
        carrier = patch.after["vector_objects"][0]
        metadata = copy.deepcopy(carrier.meta)
        current_frame = read_frame_layer(metadata[PANEL_META_KEY]["metadata"])
        metadata[PANEL_META_KEY]["metadata"] = write_frame_layer(
            metadata[PANEL_META_KEY]["metadata"],
            replace(current_frame, layout_leaf_ids=tuple(owners[panel_id])),
        )
        patch.after["vector_objects"] = [replace(carrier, meta=metadata)]
    for panel_id in records:
        if panel_id not in fresh:
            continue
        record = records[panel_id]
        obj = next(obj for _, obj in additions if obj.meta[PANEL_META_KEY]["panel_id"] == panel_id)
        commands.append(
            prepare_panel_frame_conversion(
                source_layer_id=state.frames_layer_id,
                source_object=obj,
                record=record,
                rect=rects[panel_id],
                boundaries={
                    side: _outer_ref(owners[panel_id], leaves, rects[panel_id], side, boundaries)
                    for side in ("top", "right", "bottom", "left")
                },
                page_size=tuple(map(int, page_size)),
                max_group_depth=8,
                layout_leaf_ids=tuple(owners[panel_id]),
            )
        )
    from .panel_shared_plan import _shared_mask  # noqa: PLC0415 - mutual planner dependency

    planned = replace(state, panel_records=records, panel_rects=rects)
    for group_id, part in state.panel_parts.items():
        if SHARED_KEY not in (part.values["metadata"] or {}):
            continue
        shared = read_shared_content(part.values["metadata"], group_id=group_id)
        commands.append(
            PanelLayerPatchCommand(
                group_id,
                before={"layer_mask": part.values["layer_mask"]},
                after={
                    "layer_mask": _shared_mask(
                        planned, shared.panel_ids, tuple(map(int, page_size))
                    )
                },
            )
        )
    command = PanelEditCommand(commands, label=label)
    from .panel_split_edges import requires_edge_allocation, split_edge_parts  # noqa: PLC0415

    for panel_id in state.panel_records:
        if not requires_edge_allocation(state, panel_id):
            continue
        x, y, w, h = state.panel_rects[panel_id]
        children = tuple(
            region
            for region in snapshot.regions
            if region.rect[0] >= x - 1e-6
            and region.rect[1] >= y - 1e-6
            and region.rect[0] + region.rect[2] <= x + w + 1e-6
            and region.rect[1] + region.rect[3] <= y + h + 1e-6
        )
        if len(children) > 1 and any(child.panel_id in fresh for child in children):
            split_edge_parts(state, panel_id, children, command, page_size=page_size)
    return command


def _outer_ref(leaf_ids, leaves, rect, side, boundaries):
    from .panel_merge_edges import _outer  # noqa: PLC0415

    leaf = next(leaf for leaf in leaf_ids if _outer(leaves[leaf], rect, side))
    return boundaries[(leaf, side)]
