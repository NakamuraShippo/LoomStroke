"""Keep an appearance layer's identity stable for subsequent image/metadata commands."""

from __future__ import annotations

from typing import Any

from ..commands_layer_lookup import find_layer
from ..commands_layer_tree import AddLayerCommand, DeleteLayerCommand


class AddAppearanceLayerCommand(AddLayerCommand):
    """Add a raster layer, restoring the same layer object when a bake is redone."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._undo_remove: DeleteLayerCommand | None = None
        self._active_layer_id_before: str | None = None

    def apply(self, canvas: Any) -> bool:
        if self._undo_remove is None:
            self._active_layer_id_before = getattr(canvas, "_active_layer_id", None)
            return super().apply(canvas)
        if not self._undo_remove.revert(canvas):
            return False
        activate = getattr(canvas, "set_active_layer", None)
        if callable(activate) and self._layer_id is not None:
            activate(self._layer_id)
        return True

    def revert(self, canvas: Any) -> bool:
        if not callable(getattr(canvas, "_find_parent", None)):
            # Legacy lightweight canvas adapters expose only add/remove;
            # preserve their existing undo contract without requiring tree APIs.
            return super().revert(canvas)
        if self._layer_id is None:
            return False
        undo_remove = DeleteLayerCommand(self._layer_id)
        if not undo_remove.apply(canvas):
            return False
        self._undo_remove = undo_remove
        previous = self._active_layer_id_before
        activate = getattr(canvas, "set_active_layer", None)
        if previous is not None and callable(activate) and find_layer(canvas, previous) is not None:
            activate(previous)
        return True
