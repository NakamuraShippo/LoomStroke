"""Delete a complete managed content unit through the normal delete contract."""

import copy

from loomstroke.core.command_types import Command
from loomstroke.core.layer_tree_service import LayerTreeQueryService, sync_active_markers

from .membership import subtree


class PanelContentDeleteCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(self, layer_id):
        super().__init__()
        from loomstroke.core.commands_layer_tree import DeleteLayerCommand  # noqa: PLC0415

        self.command = DeleteLayerCommand(layer_id)
        self.command._panel_raw = True
        self.layer_id = layer_id
        self.active = None
        self.selection = None
        self.last_error = ""

    def apply(self, canvas):
        self.active = canvas._active_layer_id
        self.selection = copy.deepcopy(getattr(canvas, "_vector_selection", None))
        source = canvas._get_layer_by_id(self.layer_id)
        ids = {node.id for node in subtree(source)} if source is not None else set()
        self._executed = self.command.apply(canvas)
        if self._executed and self.selection and self.selection.get("layer_id") in ids:
            canvas._vector_selection = None
        return self._executed

    def revert(self, canvas):
        if not self._executed or not self.command.revert(canvas):
            return False
        canvas._active_layer_id = self.active
        canvas._vector_selection = copy.deepcopy(self.selection)
        sync_active_markers(canvas, LayerTreeQueryService(canvas))
        self._executed = False
        return True
