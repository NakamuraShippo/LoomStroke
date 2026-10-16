"""Keep a selected text object's identity when wrapping it in a balloon unit."""

import copy

from loomstroke.core.command_types import Command


class PanelTextSelectionCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(self, object_id, layer_id):
        super().__init__()
        self.object_id, self.layer_id = object_id, layer_id
        self.before = None

    def apply(self, canvas):
        self.before = copy.deepcopy(getattr(canvas, "_vector_selection", None))
        if isinstance(self.before, dict) and self.before.get("id") == self.object_id:
            canvas._vector_selection = dict(self.before, layer_id=self.layer_id)
        canvas.set_active_layer(self.layer_id)
        self._executed = True
        return True

    def revert(self, canvas):
        canvas._vector_selection = copy.deepcopy(self.before)
        self._executed = False
        return True
