"""Prepare whole-layer panel assignment and image fitting without live mutation."""

import copy
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.image_placement import PLACEMENT_KEY, fit_image
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY, SHARED_KEY, whole_unit_source
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.commands_layer_tree import ReparentLayerCommand
from loomstroke.plugins.comic_tools.panel_edit_gate import require_editable_parts
from loomstroke.plugins.comic_tools.panel_part_snapshot import part_snapshots
from loomstroke.plugins.comic_tools.panel_repair_preview import PanelRepairPreview
from loomstroke.plugins.comic_tools.panel_source_guard import source_guard


def content_edit_command(canvas, state, operation):
    layers = tuple(canvas._flatten_layers())
    state = replace(
        state, panel_parts=part_snapshots(layers, include_layer_ids={p.id for p in layers})
    )
    item_id = whole_unit_source(canvas._layer_tree(), operation["layerId"])
    part = state.panel_parts.get(item_id)
    if part is None:
        raise PanelFrameError("content layer is missing")
    targets = []

    def visit(layer_id):
        node = state.panel_parts[layer_id]
        if {PART_KEY, SHARED_KEY} & (node.values["metadata"] or {}).keys():
            raise PanelFrameError(
                "managed frame parts or shared sources cannot be assigned as content"
            )
        targets.append(layer_id)
        for child in node.children:
            visit(child)

    visit(item_id)
    pid = operation["panelId"]
    frame = read_frame_layer(state.panel_records[pid].metadata)
    require_editable_parts(state, [*targets, frame.content_group_id])
    if operation["action"] == "fit_content":
        preview = PanelRepairPreview(
            state, state.layout, page_size=canvas.get_canvas_size(), fixed_frame=True
        )
        if item_id not in {
            unit.layer_id for unit in state.panel_contents.get(frame.content_group_id, ())
        }:
            raise PanelFrameError("content is not owned by the target panel")
        preview.fit(item_id, policy=operation["fitPolicy"])
        return preview.command()
    commands = [
        source_guard(state, page_size=canvas.get_canvas_size()),
        ReparentLayerCommand(item_id, frame.content_group_id),
    ]
    if "fitPolicy" in operation:
        image = part.values["image"]
        if part.values["layer_type"] != "raster" or image is None or image.isNull():
            raise PanelFrameError("image fitting requires a raster image layer")
        x, y, w, h = state.panel_rects[pid]
        inset = frame.clip_inset_px
        placement = fit_image(
            (image.width(), image.height()),
            (x + inset, y + inset, w - 2 * inset, h - 2 * inset),
            policy=operation["fitPolicy"],
        )
        metadata = copy.deepcopy(part.values["metadata"] or {})
        metadata[PLACEMENT_KEY] = {
            "version": 1,
            "fit_policy": placement.fit_policy,
            "source_size": list(placement.source_size),
        }
        before = {
            key: part.values[key] for key in ("transform", "offset_x", "offset_y", "metadata")
        }
        commands.append(
            PanelLayerPatchCommand(
                item_id,
                before=before,
                after={
                    "transform": placement.transform,
                    "offset_x": 0,
                    "offset_y": 0,
                    "metadata": metadata,
                },
            )
        )
    return PanelEditCommand(commands, label="Assign panel content")
