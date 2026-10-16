"""Guarded movement of registered frame parts during a topology edit."""

from loomstroke.core.command_types import Command

from .membership import PART_KEY


class PanelPartMoveCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(self, layer_id, parent_id, *, expected_parent_id):
        super().__init__()
        self.layer_id, self.parent_id = layer_id, parent_id
        self.expected_parent_id = expected_parent_id
        self._position = None
        self.last_error = ""

    def apply(self, canvas):
        tree = canvas._layer_tree()
        layer = tree.find_layer_by_id_tree(self.layer_id)
        if (
            layer is None
            or layer.parent_id != self.expected_parent_id
            or (layer.metadata or {}).get(PART_KEY, {}).get("role") not in {"edge", "restore"}
        ):
            self.last_error = "registered frame part changed during topology editing"
            return False
        if self._position is None:
            self._position = tree.find_parent(self.layer_id)[1]
        self._executed = tree.reparent_layer(self.layer_id, self.parent_id, _part_move=True)
        return self._executed

    def revert(self, canvas):
        if not self._executed:
            return False
        ok = canvas._layer_tree().reparent_layer(
            self.layer_id, self.expected_parent_id, self._position, _part_move=True
        )
        self._executed = not ok
        return ok
