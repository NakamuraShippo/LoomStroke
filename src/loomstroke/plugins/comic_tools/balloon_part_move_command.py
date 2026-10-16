"""Move one identified raster part when assembling or undoing a balloon unit."""

from loomstroke.core.command_types import Command

from .raster_parts import part_layer_balloon_id


class BalloonPartMoveCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(self, layer_id, parent_id, *, expected_parent_id, balloon_id, target_position=-1):
        super().__init__()
        self.layer_id, self.parent_id = layer_id, parent_id
        self.expected_parent_id, self.balloon_id = expected_parent_id, balloon_id
        self.position = None
        self.target_position = target_position
        self.last_error = ""

    def apply(self, canvas):
        tree = canvas._layer_tree()
        layer = tree.find_layer_by_id_tree(self.layer_id)
        if (
            layer is None
            or layer.parent_id != self.expected_parent_id
            or part_layer_balloon_id(layer) != self.balloon_id
        ):
            self.last_error = "balloon raster part changed during preparation"
            return False
        if self.position is None:
            self.position = tree.find_parent(self.layer_id)[1]
        self._executed = tree.reparent_layer(
            self.layer_id, self.parent_id, self.target_position, _part_move=True
        )
        return self._executed

    def revert(self, canvas):
        if not self._executed:
            return False
        result = canvas._layer_tree().reparent_layer(
            self.layer_id, self.expected_parent_id, self.position, _part_move=True
        )
        self._executed = not result
        return result
