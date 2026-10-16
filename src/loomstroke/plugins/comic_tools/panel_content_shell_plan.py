"""One inherited compositing shell per original group and destination."""

import uuid

from loomstroke.core.comic_panels.content_shell_command import PanelContentShellCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.command_types import BlendMode

from .panel_clip_policy import authored_clip


def content_shell_parent(state, unit, panel_id, target_id, page_size, shells, commands):
    source = state.panel_parts.get(unit.parent_id)
    if (
        panel_id is None
        or not source
        or (source.values["metadata"] or {}).get(PART_KEY, {}).get("role") != "content"
    ):
        return target_id
    if unit.parent_id == target_id:
        return target_id
    authored = authored_clip(state, panel_id, page_size)
    if not (
        authored
        or source.values["opacity"] != 1
        or source.values["blend_mode"] != BlendMode.NORMAL
        or source.values["effect_type"]
    ):
        return target_id
    key = source.layer_id, target_id
    if key not in shells:
        layer_id = f"panel-content-shell-{uuid.uuid4().hex}"
        shells[key] = layer_id
        commands.append(
            PanelContentShellCommand(source.layer_id, layer_id, target_id, preserve_mask=authored)
        )
    return shells[key]
