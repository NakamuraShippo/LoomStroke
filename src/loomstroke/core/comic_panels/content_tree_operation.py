"""Route existing duplicate/delete gestures to whole comic content units."""

from .content_delete_command import PanelContentDeleteCommand
from .content_duplicate_command import PanelContentDuplicateCommand
from .membership import (
    FRONT_KEY,
    PART_KEY,
    SHARED_KEY,
    UNIT_KEY,
    owner_for_parent,
    subtree,
    whole_unit_source,
)
from .models import PanelFrameError
from .orphan_blank_panel import is_orphan_blank_panel
from .panel_gate import availability, panel_groups
from .shared_metadata import read_shared_content


def _shared_owner(query, source):
    seen = set()
    while source is not None:
        if source.id in seen:
            raise PanelFrameError("cyclic panel membership ancestry")
        seen.add(source.id)
        if SHARED_KEY in (source.metadata or {}):
            return read_shared_content(source.metadata, group_id=source.id)
        source = query.find_layer_by_id_tree(source.parent_id) if source.parent_id else None
    return None


def content_tree_operation(canvas, layer_id, operation):
    if not callable(getattr(canvas, "_layer_tree", None)):
        return None
    query = canvas._layer_tree()
    source = query.find_layer_by_id_tree(whole_unit_source(query, layer_id))
    if source is None:
        return None
    metadata = source.metadata or {}
    nodes = tuple(subtree(source))
    if any(PART_KEY in (node.metadata or {}) for node in nodes):
        if operation == "delete" and is_orphan_blank_panel(query, source):
            if availability(query, source)[1]:
                raise PanelFrameError("orphan panel is locked or has an unfinished mask edit")
            return PanelContentDeleteCommand(source.id)
        raise PanelFrameError("use a panel operation to edit managed frame parts")
    shared = _shared_owner(query, source)
    if not (
        shared
        or {UNIT_KEY, SHARED_KEY, FRONT_KEY} & metadata.keys()
        or any(UNIT_KEY in (node.metadata or {}) for node in nodes)
        or owner_for_parent(query, source.parent_id)
    ):
        return None
    selected = query.find_layer_by_id_tree(layer_id)
    if any(availability(query, node)[1] for node in nodes) or availability(query, selected)[1]:
        raise PanelFrameError("content or its panel is locked or has an unfinished mask edit")
    if shared:
        frames = panel_groups(query)
        for panel_id in shared.panel_ids:
            frame = frames.get(panel_id)
            if frame is None:
                raise PanelFrameError("shared display owner is missing")
            owner = query.find_layer_by_id_tree(frame.panel_group_id)
            if availability(query, owner)[1]:
                raise PanelFrameError(
                    "shared display panel is locked or has an unfinished mask edit"
                )
    if operation == "delete":
        return PanelContentDeleteCommand(source.id)
    return PanelContentDuplicateCommand(
        source.id,
        source.parent_id,
        expected_version=source.texture_version,
        expected_parent_id=source.parent_id,
        front_panel_id=(metadata.get(FRONT_KEY) or {}).get("panel_id"),
        name=f"{source.name} Copy",
    )


def apply_content_tree_operation(command, canvas, operation):
    try:
        if command._panel_command is None:
            source_id = command._source_id if operation == "copy" else command._layer_id
            command._panel_command = content_tree_operation(canvas, source_id, operation)
        if command._panel_command is None:
            return None
        if operation == "copy":
            command._new_layer_id = command._panel_command.layer_id
        result = command._panel_command.apply(canvas)
        command.last_error = command._panel_command.last_error
        return result
    except PanelFrameError as exc:
        command.last_error = str(exc)
        return False
