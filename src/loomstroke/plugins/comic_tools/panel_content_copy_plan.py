"""Independent split copies retain their original clip/display policy."""

import uuid

from loomstroke.core.comic_panels.content_copy_command import PanelContentCopyCommand
from loomstroke.core.comic_panels.membership import FRONT_KEY, PART_KEY
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand


def copy_content_commands(state, unit, panel_id, content_group_id, panel_group_id):
    part = state.panel_parts[unit.layer_id]
    parent = state.panel_parts.get(unit.parent_id)
    commands = []
    front_panel_id = None
    target = content_group_id
    if (part.values["metadata"] or {}).get(FRONT_KEY):
        target = unit.parent_id
        front_panel_id = panel_id
    elif parent and (parent.values["metadata"] or {}).get(PART_KEY, {}).get("role") == "overflow":
        target = next(
            (
                p.layer_id
                for p in state.panel_parts.values()
                if p.parent_id == panel_group_id
                and (p.values["metadata"] or {}).get(PART_KEY, {}).get("role") == "overflow"
            ),
            None,
        )
        if target is None:
            target = f"panel-overflow-{uuid.uuid4().hex}"
            commands.append(
                PanelLayerInsertCommand(
                    target,
                    parent_id=panel_group_id,
                    attributes={
                        "name": "Overflow",
                        "layer_type": "group",
                        "metadata": {PART_KEY: {"version": 1, "role": "overflow"}},
                    },
                )
            )
    commands.append(
        PanelContentCopyCommand(
            unit.layer_id,
            target,
            expected_version=unit.texture_version,
            expected_parent_id=unit.parent_id,
            front_panel_id=front_panel_id,
        )
    )
    return commands
