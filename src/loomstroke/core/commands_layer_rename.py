"""Undoable layer rename shared by the panel, actions, channels, and text tool."""

from __future__ import annotations

import contextlib
from inspect import getattr_static
from typing import Any, TypeGuard

from .command_types import Command
from .commands_layer_lookup import find_layer
from .events import Events

__all__ = ["RenameLayerCommand"]


class RenameLayerCommand(Command):
    def __init__(
        self,
        layer_id: str,
        name: str,
        *,
        merge_key: str | None = None,
        _notify_document_on_submit: bool = True,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new = name.strip()
        self._old: str | None = None
        self._notify_document_on_submit = _notify_document_on_submit
        self._defer_notifications = False

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

    def _notify(self, canvas: Any, layer: Any) -> None:
        if self._defer_notifications:
            return
        publish = getattr(canvas, "_publish_layer_event", None)
        if callable(publish):
            with contextlib.suppress(Exception):  # event bus is best effort
                publish(Events.LAYER_CHANGED, layer=layer, change_type="property_changed")


_RENAME_DEFAULT_METHODS = tuple(
    (name, getattr(RenameLayerCommand, name)) for name in ("apply", "revert", "_notify")
)


def _is_standard_rename_command(command: Any) -> TypeGuard[RenameLayerCommand]:
    """Only the original implementation participates in checked commitment."""
    return type(command) is RenameLayerCommand and all(
        getattr_static(command, name, None) is method for name, method in _RENAME_DEFAULT_METHODS
    )
