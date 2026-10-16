"""Explicit, undoable grouping of legacy balloon components for logical layer rows."""

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.membership import UNIT_KEY, subtree
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.panel_gate import availability

from .model import balloon_record_from_object, balloon_text_link_from_object
from .panel_balloon_adoption_plan import AdoptCreatedBalloonCommand
from .panel_source_guard import source_guard
from .raster_parts import part_layer_balloon_id
from .service import ComicPageController


def unit_layer_id(state, balloon_id):
    body_id = state.balloon_sources.get(balloon_id)
    part = state.panel_parts.get(body_id)
    parent = state.panel_parts.get(part.parent_id) if part else None
    marker = (parent.values["metadata"] or {}).get(UNIT_KEY) if parent else None
    return parent.layer_id if marker and marker.get("balloon_id") == balloon_id else None


def unit_edits_allowed(canvas, object_ids):
    """Comic edits must respect the lock of their logical row and its parts."""
    if not object_ids or not callable(getattr(canvas, "_layer_tree", None)):
        return True
    query = canvas._layer_tree()
    for layer_id, obj in canvas.get_vector_objects(include_hidden=True):
        if obj.id not in object_ids:
            continue
        node = query.find_layer_by_id_tree(layer_id)
        seen = set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            if UNIT_KEY in (node.metadata or {}):
                if any(availability(query, part)[1] for part in subtree(node)):
                    return False
                break
            node = query.find_layer_by_id_tree(node.parent_id) if node.parent_id else None
    return True


def legacy_balloon_ids(canvas, layer_id):
    getter = getattr(canvas, "_get_layer_by_id", None)
    if not callable(getter):
        return []
    part = getter(layer_id)
    part_id = part_layer_balloon_id(part)
    if not part_id and not any(
        balloon_record_from_object(obj) or balloon_text_link_from_object(obj)
        for obj in (getattr(part, "vector_objects", None) or [])
    ):
        return []
    controller = ComicPageController()
    state = controller.read_state(canvas, page_size=canvas.get_canvas_size())
    return [
        balloon_id
        for balloon_id in state.balloons
        if not unit_layer_id(state, balloon_id)
        and (
            layer_id in {state.balloon_sources.get(balloon_id), state.text_sources.get(balloon_id)}
            or balloon_id == part_id
        )
    ]


def group_legacy_balloons_command(canvas, layer_id):
    controller = ComicPageController()
    state = controller.read_state(canvas, page_size=canvas.get_canvas_size())
    commands = [source_guard(state)]
    for balloon_id in legacy_balloon_ids(canvas, layer_id):
        record = state.balloons[balloon_id]
        target = state.panel_records.get(record.panel_id)
        frame = read_frame_layer(target.metadata) if target else None
        commands.append(
            AdoptCreatedBalloonCommand(
                controller, balloon_id, record.panel_id, standalone=frame is None
            )
        )
    return PanelEditCommand(commands, label="Group balloon objects")
