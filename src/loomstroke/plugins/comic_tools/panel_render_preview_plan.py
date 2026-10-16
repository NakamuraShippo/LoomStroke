"""Read prepared edit plans for a temporary view without applying commands."""

from dataclasses import replace

from loomstroke.core.comic_panels.content_move_command import translated_mask
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.image_placement import PLACEMENT_KEY
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import BALLOON_KEY
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.commands_layer_transform import _translate_vec_obj

from .canvas_balloon_translate import translate_balloon_metadata


def geometry_preview_parts(repair):
    updates, insertions = {}, []

    def visit(command):
        if isinstance(command, PanelEditCommand):
            for child in command.commands:
                visit(child)
        elif isinstance(command, PanelLayerPatchCommand):
            updates.setdefault(command.layer_id, {}).update(command.after)
        elif isinstance(command, PanelLayerInsertCommand):
            insertions.append(command._layer)
            updates[command.layer_id] = {
                "vector_objects": command._layer.vector_objects,
                "metadata": command._layer.metadata,
                "layer_mask": command._layer.layer_mask,
            }

    visit(repair.geometry_command())
    return updates, tuple(insertions)


def content_preview_parts(repair):
    updates = {}
    for panel_id, (dx, dy) in repair.clip_moves.items():
        if panel_id in repair.regenerated_clips:
            continue
        from loomstroke.core.comic_panels.metadata import read_frame_layer  # noqa: PLC0415

        frame = read_frame_layer(repair.state.panel_records[panel_id].metadata)
        part = repair.state.panel_parts[frame.content_group_id]
        if part.values["layer_mask"] is not None and (dx or dy):
            updates[part.layer_id] = {
                "layer_mask": translated_mask(part.values["layer_mask"], dx, dy)
            }
    for item_id in set(repair.image_repairs) | set(repair.moves):
        part = repair.state.panel_parts[item_id]
        dx, dy = repair.moves.get(item_id, (0, 0))
        if PLACEMENT_KEY in (part.values["metadata"] or {}):
            placement = repair.image_repairs.get(item_id)
            transform = placement.transform if placement else part.values["transform"]
            updates[item_id] = {
                "transform": transform.with_translation(transform.tx + dx, transform.ty + dy),
                **moved_masks(part.values, dx, dy),
            }
            continue

        def moved(layer_id, dx=dx, dy=dy):
            source = repair.state.panel_parts[layer_id]
            values = source.values
            if values["layer_type"] == "vector":
                objects = []
                for obj in values["vector_objects"]:
                    translated = _translate_vec_obj(obj, dx, dy)
                    if BALLOON_KEY in obj.meta:
                        translated = replace(
                            translated, meta=translate_balloon_metadata(translated.meta, dx, dy)
                        )
                    objects.append(translated)
                updates[layer_id] = {"vector_objects": objects}
            elif values["image"] is not None:
                # Other raster units use the ordinary MoveLayer offset contract.
                transform = values["transform"]
                updates[layer_id] = {
                    "offset_x": values["offset_x"] + dx,
                    "offset_y": values["offset_y"] + dy,
                    "transform": transform.with_translation(
                        (
                            transform.tx
                            if transform != LayerTransform.identity()
                            else values["offset_x"]
                        )
                        + dx,
                        (
                            transform.ty
                            if transform != LayerTransform.identity()
                            else values["offset_y"]
                        )
                        + dy,
                    ),
                }
            updates.setdefault(layer_id, {}).update(moved_masks(values, dx, dy))
            for child_id in source.children:
                moved(child_id)

        moved(item_id)
    return updates


def moved_masks(values, dx, dy):
    return {
        name: translated_mask(values[name], dx, dy)
        for name in ("layer_mask", "effect_mask")
        if (dx or dy) and values.get(name) is not None
    }
