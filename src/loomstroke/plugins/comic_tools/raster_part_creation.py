"""Stable raster-part insertions reused by balloon creation Undo/Redo."""

import uuid

from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.events import Events

from .raster_parts import part_layer_marker


def create_part_layer(canvas, balloon_id, image, insertions):
    command = insertions.get(balloon_id)
    if command is None:
        command = PanelLayerInsertCommand(
            f"balloon-raster-{uuid.uuid4().hex}",
            parent_id=None,
            attributes={
                "name": f"フキダシ画像 {balloon_id}",
                "image": image,
                "metadata": part_layer_marker(balloon_id),
            },
        )
        insertions[balloon_id] = command
    if not command.apply(canvas):
        raise RuntimeError(command.last_error)
    layer = canvas._get_layer_by_id(command.layer_id)
    canvas._publish_layer_event(Events.LAYER_ADDED, layer=layer, change_type="added")
    return layer
