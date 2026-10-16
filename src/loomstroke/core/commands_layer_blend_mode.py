"""Undoable layer blend-mode change (wraps the canvas' direct ``set_layer_blend_mode``)."""

from __future__ import annotations

from typing import Any

from .command_types import Command

__all__ = ["SetLayerBlendModeCommand"]


class SetLayerBlendModeCommand(Command):
    def __init__(self, layer_id: str, mode: str, *, merge_key: str | None = None) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new = str(mode)
        self._old: str | None = None

    def apply(self, canvas: Any) -> bool:
        getter = getattr(canvas, "get_layer_info", None)
        setter = getattr(canvas, "set_layer_blend_mode", None)
        if not callable(getter) or not callable(setter):
            return False
        info = getter(self.layer_id)
        if not isinstance(info, dict):
            return False
        self._old = str(info.get("blend_mode", "normal"))
        setter(self.layer_id, self._new)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed or self._old is None:
            return False
        setter = getattr(canvas, "set_layer_blend_mode", None)
        if not callable(setter):
            return False
        setter(self.layer_id, self._old)
        self._executed = False
        return True
