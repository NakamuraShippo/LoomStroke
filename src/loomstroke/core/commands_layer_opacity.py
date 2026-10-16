"""Undoable layer opacity change (wraps the canvas' direct ``set_layer_opacity``)."""

from __future__ import annotations

from typing import Any

from .command_types import Command

__all__ = ["SetLayerOpacityCommand"]


class SetLayerOpacityCommand(Command):
    def __init__(self, layer_id: str, opacity: float, *, merge_key: str | None = None) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new = max(0.0, min(1.0, float(opacity)))
        self._old: float | None = None

    def apply(self, canvas: Any) -> bool:
        info = _layer_info(canvas, self.layer_id)
        setter = getattr(canvas, "set_layer_opacity", None)
        if info is None or not callable(setter):
            return False
        self._old = float(info.get("opacity", 1.0))
        setter(self.layer_id, self._new)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed or self._old is None:
            return False
        setter = getattr(canvas, "set_layer_opacity", None)
        if not callable(setter):
            return False
        setter(self.layer_id, self._old)
        self._executed = False
        return True


def _layer_info(canvas: Any, layer_id: str) -> dict[str, Any] | None:
    getter = getattr(canvas, "get_layer_info", None)
    if not callable(getter):
        return None
    info = getter(layer_id)
    return info if isinstance(info, dict) else None
