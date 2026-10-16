"""Duplicate content and select the new unit, restoring selection on Undo."""

import copy

from loomstroke.core.layer_tree_service import LayerTreeQueryService, sync_active_markers

from .content_copy_command import PanelContentCopyCommand


class PanelContentDuplicateCommand(PanelContentCopyCommand):
    def apply(self, canvas):
        self.active = canvas._active_layer_id
        self.selection = copy.deepcopy(getattr(canvas, "_vector_selection", None))
        if not super().apply(canvas):
            return False
        canvas._vector_selection = None
        canvas.set_active_layer(self.layer_id)
        return True

    def revert(self, canvas):
        if not super().revert(canvas):
            return False
        canvas._active_layer_id = self.active
        canvas._vector_selection = copy.deepcopy(self.selection)
        sync_active_markers(canvas, LayerTreeQueryService(canvas))
        return True
