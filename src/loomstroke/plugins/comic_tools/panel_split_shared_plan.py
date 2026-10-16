"""Qualify and group page-fixed shared backgrounds before any edit is applied."""

import numpy as np

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.command_types import BlendMode, LayerTransform
from loomstroke.core.commands_layer_tree import ReparentLayerCommand
from loomstroke.core.mask_coverage import mask_coverage_alpha8

from .panel_content_shell_plan import content_shell_parent
from .panel_shared_plan import share_content_commands


def shared_split_reason(preview):  # noqa: PLR0911 - report distinct unsupported compositing states
    frame = read_frame_layer(preview.state.panel_records[preview.original_panel_id].metadata)
    units = preview.state.panel_contents.get(frame.content_group_id, ())
    selected = [u for u in units if preview.image_policies.get(u.layer_id, "move") == "shared"]
    if not selected:
        return ""
    # A common page plane cannot reproduce interleaved foreground units or an
    # inherited group opacity applied partly to shared and partly to moved ink.
    shared_ids = {u.layer_id for u in selected}
    source = preview.state.panel_parts[frame.content_group_id]
    values = source.values
    if any(u.layer_id not in shared_ids for u in units[: len(selected)]) and not _fully_masked(
        values
    ):
        return "shared backgrounds must precede the remaining panel content"
    if values["blend_mode"] != BlendMode.NORMAL:
        return "shared content cannot preserve the owner compositing"
    if values["transform"] != LayerTransform.identity() or values["offset_x"] or values["offset_y"]:
        return "shared content cannot preserve the owner compositing"
    if len(selected) != len(units) and (
        values["opacity"] != 1 or values["blend_mode"] != BlendMode.NORMAL or values["effect_type"]
    ):
        return "shared content cannot split the original group compositing"
    if values["clipping"]:
        return "shared content cannot preserve this clipping chain"
    layer_id, seen = source.parent_id, set()
    while layer_id:
        part = preview.state.panel_parts.get(layer_id)
        if part is None or layer_id in seen:
            return "shared content has missing or cyclic ancestry"
        seen.add(layer_id)
        values = part.values
        if (
            not values["visible"]
            or values["opacity"] != 1
            or values["blend_mode"] != BlendMode.NORMAL
            or values["effect_type"]
            or values["clipping"]
            or (values["layer_mask"] is not None and values["mask_enabled"])
            or values["transform"] != LayerTransform.identity()
            or values["offset_x"]
            or values["offset_y"]
        ):
            return "shared content cannot preserve the owner compositing"
        layer_id = part.parent_id
    return ""


def _fully_masked(values):
    mask = values["layer_mask"]
    if mask is None or not values["mask_enabled"]:
        return False
    coverage = mask_coverage_alpha8(mask)
    pixels = np.frombuffer(coverage.constBits(), dtype=np.uint8).reshape(
        coverage.height(), coverage.bytesPerLine()
    )[:, : coverage.width()]
    return not np.any(pixels)


def split_shared_commands(preview, planned, unit, shared_groups, shells):
    panel_ids = [c.panel_id for c in preview.children]
    key = unit.parent_id, tuple(panel_ids)
    commands = []
    shared = shared_groups.get(key)
    if shared is None:
        commands, shared = share_content_commands(
            planned,
            unit.layer_id,
            panel_ids=panel_ids,
            page_id=preview.page_id,
            page_size=preview.page_size,
        )
        commands.pop()  # Install one ordered reparent after any compositing shell.
        shared_groups[key] = shared
    target = content_shell_parent(
        preview.state,
        unit,
        preview.original_panel_id,
        shared.group_id,
        preview.page_size,
        shells,
        commands,
    )
    commands.append(ReparentLayerCommand(unit.layer_id, target))
    return commands


def require_shared_split(preview):
    reason = shared_split_reason(preview)
    if reason:
        raise PanelFrameError(reason)
