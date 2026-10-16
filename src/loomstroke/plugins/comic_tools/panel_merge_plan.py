"""Rectangle-adjacent merge independent of the split tree's sibling history."""

from __future__ import annotations

import copy
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY, SHARED_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.regions import merge_regions
from loomstroke.core.comic_panels.shared_metadata import read_shared_content, write_shared_content

from . import layout_tree as lt
from .model import PANEL_META_KEY, merged_layer_metadata_with_layout
from .panel_edit_gate import require_panel_editable
from .panel_frame_layout_adapter import layout_snapshot
from .panel_resize_plan import resize_panel_commands
from .panel_source_guard import source_guard


def merge_panel_command(  # noqa: PLR0912, PLR0915 - one guarded merge sequence
    state, primary_id, secondary_id, *, page_size
):
    if state.panel_polygons:
        raise PanelFrameError("merge inclined panels requires a convex ownership plan")
    owners = {}
    frames = {}
    for panel_id, record in state.panel_records.items():
        frame = read_frame_layer(record.metadata)
        if frame is None:
            raise PanelFrameError("merge requires materialized adjacent panels")
        frames[panel_id] = frame
        owners[panel_id] = frame.layout_leaf_ids
    if primary_id not in frames or secondary_id not in frames:
        raise PanelFrameError("merge target is missing")
    for panel_id in (primary_id, secondary_id):
        require_panel_editable(state, panel_id, geometry=True)
    leaves = lt.compute_panel_rects(state.layout, *page_size)
    regions = merge_regions(
        leaves, owners, primary_id, secondary_id, gutter_px=state.layout.gutter_px
    )
    region = next(r for r in regions if r.panel_id == primary_id)
    snapshot = layout_snapshot(state.layout, page_size, state.panel_records)
    refs = {(leaf, side): ref for leaf, side, ref in snapshot.boundaries}
    x, y, w, h = region.rect
    sides = {}
    for side in ("top", "right", "bottom", "left"):
        leaf = next(
            leaf
            for leaf in region.layout_leaf_ids
            if {
                "top": abs(leaves[leaf][1] - y) < 1e-6,
                "left": abs(leaves[leaf][0] - x) < 1e-6,
                "right": abs(leaves[leaf][0] + leaves[leaf][2] - x - w) < 1e-6,
                "bottom": abs(leaves[leaf][1] + leaves[leaf][3] - y - h) < 1e-6,
            }[side]
        )
        sides[side] = refs[(leaf, side)]
    commands = [source_guard(state, page_size=page_size)]
    resized = resize_panel_commands(state, primary_id, region.rect, sides, page_size=page_size)
    from .panel_merge_edges import merge_edge_parts  # noqa: PLC0415

    edges, raw, part_commands = merge_edge_parts(
        state, (primary_id, secondary_id), frames, region.rect, sides, page_size=page_size
    )
    from .panel_merge_content import merge_content_bundle  # noqa: PLC0415

    bundle = merge_content_bundle(state, (primary_id, secondary_id), frames, region.rect, page_size)
    if bundle:
        commands.extend(bundle[1])
    else:
        commands.append(resized[0])
    commands.extend(part_commands)
    commands.append(resized[-1])
    # Amend the prepared carrier patch, preserving all extension payloads.
    patch = commands[-1]
    carrier = patch.after["vector_objects"][0]
    payload = copy.deepcopy(carrier.meta)
    metadata = payload[PANEL_META_KEY]["metadata"]
    frame = replace(read_frame_layer(metadata), layout_leaf_ids=region.layout_leaf_ids, edges=edges)
    if bundle:
        frame = replace(frame, content_group_id=bundle[0].content_group_id)
    metadata = write_frame_layer(raw, frame)
    metadata["frame_layer"].setdefault("retired_panel_group_ids", []).append(
        frames[secondary_id].panel_group_id
    )
    payload[PANEL_META_KEY]["metadata"] = metadata
    patch.after["vector_objects"] = [replace(carrier, meta=payload)]
    secondary = frames[secondary_id]
    secondary_units = tuple(
        unit
        for unit in state.panel_contents.get(secondary.content_group_id, ())
        if not bundle or unit.layer_id not in state.panel_parts[secondary.content_group_id].children
    )
    for unit in secondary_units:
        from .panel_display_plan import content_owner_commands  # noqa: PLC0415

        commands.extend(
            content_owner_commands(state, unit.layer_id, primary_id, frame.content_group_id)
        )
    source_id, _ = state.panel_sources[secondary_id]
    part = state.panel_parts[source_id]
    marker = copy.deepcopy(part.values["metadata"])
    marker[PART_KEY]["role"] = "restore"
    commands.append(
        PanelLayerPatchCommand(
            source_id, before={"metadata": part.values["metadata"]}, after={"metadata": marker}
        )
    )
    group = state.panel_parts[secondary.panel_group_id]
    group_metadata = copy.deepcopy(group.values["metadata"])
    group_metadata[PART_KEY]["role"] = "retired_panel"
    commands.append(
        PanelLayerPatchCommand(
            group.layer_id,
            before={"metadata": group.values["metadata"], "visible": group.values["visible"]},
            after={"metadata": group_metadata, "visible": False},
        )
    )
    layout = replace(
        state.layout, reading_order=tuple(p for p in state.reading_order if p != secondary_id)
    )
    for group_id, part in state.panel_parts.items():
        if SHARED_KEY not in (part.values["metadata"] or {}):
            continue
        shared = read_shared_content(part.values["metadata"], group_id=group_id)
        if secondary_id in shared.panel_ids:
            targets = tuple(
                dict.fromkeys(
                    primary_id if pid == secondary_id else pid for pid in shared.panel_ids
                )
            )
            commands.append(
                PanelLayerPatchCommand(
                    group_id,
                    before={"metadata": part.values["metadata"]},
                    after={
                        "metadata": write_shared_content(
                            part.values["metadata"], replace(shared, panel_ids=targets)
                        )
                    },
                )
            )
    commands.append(
        PanelLayerPatchCommand(
            state.frames_layer_id,
            before={"metadata": state.frames_layer_metadata},
            after={
                "metadata": merged_layer_metadata_with_layout(
                    state.frames_layer_metadata, lt.layout_to_payload(layout)
                )
            },
        )
    )
    return PanelEditCommand(commands, label="Merge adjacent panels")
