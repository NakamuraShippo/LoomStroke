"""Undoable set / clear of ONE key in a layer's ``metadata`` dict (plugin namespaces)."""

from __future__ import annotations

import copy
from typing import Any

from .command_types import Command
from .commands_layer_lookup import find_layer

__all__ = ["SetLayerMetadataKeyCommand"]

_MISSING = object()


class SetLayerMetadataKeyCommand(Command):
    def __init__(
        self, layer_id: str, key: str, value: Any, *, merge_key: str | None = None
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self.key = key
        self.value = copy.deepcopy(value)
        self._previous: Any = _MISSING

    def apply(self, canvas: Any) -> bool:
        layer = find_layer(canvas, self.layer_id)
        if layer is None:
            return False
        current = getattr(layer, "metadata", None)
        if isinstance(current, dict) and self.key in current:
            self._previous = copy.deepcopy(current[self.key])
        else:
            self._previous = _MISSING
        self._write(layer, self.value)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        layer = find_layer(canvas, self.layer_id)
        if layer is None:
            return False
        self._write(layer, None if self._previous is _MISSING else self._previous)
        self._executed = False
        return True

    def _write(self, layer: Any, value: Any) -> None:
        current = getattr(layer, "metadata", None)
        merged = dict(current) if isinstance(current, dict) else {}
        if value is None:
            merged.pop(self.key, None)
        else:
            merged[self.key] = copy.deepcopy(value)
        layer.metadata = merged or None
