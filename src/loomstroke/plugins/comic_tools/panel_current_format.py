"""Prepare the current frame representation inside an operation's single command."""

from collections import Counter
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.metadata import read_frame_layer

from .model import panel_record_from_object
from .panel_frame_conversion import prepare_panel_frame_conversion
from .panel_frame_layout_adapter import layout_snapshot
from .panel_geometry_projection import project_panel_geometry
from .panel_source_guard import source_guard


def current_panels(state, *, page_size):
    """Project unconverted panels without changing the document or history."""
    from .service import ComicPageController  # noqa: PLC0415 - avoid planner/service cycle

    controller = ComicPageController(page_key="current-frame-preparation")
    conversions = tuple(
        controller.panel_frame_conversion_command(
            state, panel_id, page_size=tuple(map(int, page_size)), max_group_depth=8
        )
        for panel_id, record in state.panel_records.items()
        if read_frame_layer(record.metadata) is None
    )
    if not conversions:
        return state, ()
    planned = project_panel_geometry(
        state, PanelEditCommand(conversions, label="Prepare current frames"), page_size
    )
    # Materialization increments each source once. Match the actual post-prefix
    # revisions so the following planner's source preflight stays strict.
    bumps = Counter(command._source_id for command in conversions)
    planned = replace(
        planned,
        panel_parts={
            key: replace(part, texture_version=part.texture_version + bumps[key])
            for key, part in planned.panel_parts.items()
        },
    )
    return planned, (source_guard(state, page_size=page_size), *conversions)


def current_layout_command(controller, state, layout, *, page_size):
    """Create frame groups directly, including their registry, in one Undo."""
    command = controller.layout_command(state, layout, page_size=page_size)
    if any(read_frame_layer(record.metadata) for record in state.panel_records.values()):
        return command
    objects = {
        record.panel_id: (layer_id, obj, record)
        for layer_id, obj in (*command._additions, *command._replacements)
        if (record := panel_record_from_object(obj)) is not None
    }
    snapshot = layout_snapshot(layout, page_size, {key: value[2] for key, value in objects.items()})
    conversions = []
    for region in snapshot.regions:
        layer_id, obj, record = objects[region.panel_id]
        obj.meta.setdefault("layer_id", layer_id)
        conversions.append(
            prepare_panel_frame_conversion(
                source_layer_id=layer_id,
                source_object=obj,
                record=record,
                rect=region.rect,
                boundaries={
                    side: ref for leaf, side, ref in snapshot.boundaries if leaf == region.panel_id
                },
                page_size=tuple(map(int, page_size)),
                max_group_depth=8,
                layout_leaf_ids=region.layout_leaf_ids,
            )
        )
    return PanelEditCommand(
        [source_guard(state, page_size=page_size), command, *conversions],
        label=command.label,
    )
