"""Undoable layer rename (the layer panel's inline rename is not command-backed)."""

from __future__ import annotations

import contextlib
from typing import Any

from .command_types import Command
from .commands_layer_lookup import find_layer
from .events import Events

__all__ = ["RenameLayerCommand"]


class RenameLayerCommand(Command):
    def __init__(self, layer_id: str, name: str, *, merge_key: str | None = None) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new = name.strip()
        self._old: str | None = None

    def apply(self, canvas: Any) -> bool:
        layer = find_layer(canvas, self.layer_id)
        if layer is None or not self._new:
            return False
        self._old = str(getattr(layer, "name", "") or "")
        layer.name = self._new
        self._executed = True
        self._notify(canvas, layer)
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed or self._old is None:
            return False
        layer = find_layer(canvas, self.layer_id)
        if layer is None:
            return False
        layer.name = self._old
        self._executed = False
        self._notify(canvas, layer)
        return True

    @staticmethod
    def _notify(canvas: Any, layer: Any) -> None:
        publish = getattr(canvas, "_publish_layer_event", None)
        if callable(publish):
            with contextlib.suppress(Exception):  # event bus is best effort
                publish(Events.LAYER_CHANGED, layer=layer, change_type="property_changed")
