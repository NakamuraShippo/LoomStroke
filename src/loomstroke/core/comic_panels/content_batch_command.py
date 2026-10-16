"""Atomic ordinary copy/delete of distinct whole comic content units."""

from .content_tree_operation import content_tree_operation
from .edit_draft import PanelEditCommand
from .membership import whole_unit_source
from .models import PanelFrameError


def content_batch_command(canvas, selected_ids, operation):
    """Return None for ordinary selections; validate all comic rows before editing."""
    if operation not in {"copy", "delete"}:
        raise PanelFrameError("unknown content tree operation")
    if len(selected_ids) < 2 or not callable(getattr(canvas, "_layer_tree", None)):
        return None
    query = canvas._layer_tree()
    prepared = {}
    managed = False
    for selected_id in selected_ids:
        if query.find_layer_by_id_tree(selected_id) is None:
            raise PanelFrameError("selected content no longer exists")
        command = content_tree_operation(canvas, selected_id, operation)
        managed |= command is not None
        unit_id = whole_unit_source(query, selected_id)
        prepared.setdefault(unit_id, command)
    if not managed:
        return None
    # Selection can include an ordinary container and a balloon inside it.
    # Retain only selected roots, after validating every originally selected row.
    ids = []
    for unit_id in prepared:
        node = query.find_layer_by_id_tree(unit_id)
        seen = {unit_id}
        while node.parent_id and node.parent_id not in prepared:
            if node.parent_id in seen:
                raise PanelFrameError("cyclic panel membership ancestry")
            seen.add(node.parent_id)
            node = query.find_layer_by_id_tree(node.parent_id)
            if node is None:
                raise PanelFrameError("selected content parent is missing")
        if not node.parent_id:
            ids.append(unit_id)
    return PanelContentBatchCommand(tuple(selected_ids), ids, prepared, operation)


class PanelContentBatchCommand(PanelEditCommand):
    def __init__(self, selected_ids, ids, prepared, operation):
        from loomstroke.core.commands_layer_tree import (  # noqa: PLC0415
            DeleteLayerCommand,
            DuplicateLayerCommand,
        )

        command_type = DuplicateLayerCommand if operation == "copy" else DeleteLayerCommand
        commands = [prepared[layer_id] or command_type(layer_id) for layer_id in ids]
        super().__init__(commands, label=f"{operation.title()} selected comic content")
        self.selected_ids = selected_ids
        self.operation = operation

    def apply(self, canvas):
        # Redo must also respect a newly locked panel or child.
        try:
            for layer_id in self.selected_ids:
                if canvas._get_layer_by_id(layer_id) is None:
                    raise PanelFrameError("selected content no longer exists")
                content_tree_operation(canvas, layer_id, self.operation)
        except PanelFrameError as exc:
            self.last_error = str(exc)
            return False
        return super().apply(canvas)
