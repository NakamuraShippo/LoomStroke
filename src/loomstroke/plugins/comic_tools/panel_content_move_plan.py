"""Move a whole content unit and keep balloon editing records in sync."""

from dataclasses import replace

from loomstroke.core.comic_panels.content_move_command import PanelContentMoveCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import BALLOON_KEY
from loomstroke.core.commands_layer_transform import _translate_vec_obj

from .canvas_balloon_translate import translate_balloon_metadata


def content_move_commands(state, item_id, dx, dy):
    commands = [PanelContentMoveCommand(dx, dy, layer_id=item_id)]

    def visit(layer_id):
        part = state.panel_parts[layer_id]
        source = part.values["vector_objects"]
        if any(BALLOON_KEY in obj.meta for obj in source):
            # MoveLayerCommand already translates paths and text/shape frames.
            # Its generic vector path does not know the comic editing record.
            moved = [_translate_vec_obj(obj, dx, dy) for obj in source]
            updated = [
                (
                    replace(obj, meta=translate_balloon_metadata(obj.meta, dx, dy))
                    if BALLOON_KEY in obj.meta
                    else obj
                )
                for obj in moved
            ]
            commands.append(
                PanelLayerPatchCommand(
                    layer_id,
                    before={"vector_objects": moved},
                    after={"vector_objects": updated},
                )
            )
        for child in part.children:
            visit(child)

    visit(item_id)
    return commands
