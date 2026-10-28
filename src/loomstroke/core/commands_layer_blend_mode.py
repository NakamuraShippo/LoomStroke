"""Undoable layer blend-mode change (wraps the canvas' direct ``set_layer_blend_mode``)."""

from __future__ import annotations

import contextlib
from inspect import getattr_static
from typing import Any, TypeGuard

from .command_types import BlendMode, Command
from .commands_layer_lookup import find_layer
from .events import Events

__all__ = ["SetLayerBlendModeCommand"]


class SetLayerBlendModeCommand(Command):
    def __init__(self, layer_id: str, mode: str, *, merge_key: str | None = None) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new = str(mode)
        self._old: str | None = None
        self._checked_values: tuple[str, str] | None = None
        self._submitting = False

    def apply(self, canvas: Any) -> bool:
        if self._checked_values is not None:
            return self._restore_checked(canvas, use_after=True)
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
        if self._checked_values is not None:
            return self._restore_checked(canvas, use_after=False)
        if not self._executed or self._old is None:
            return False
        setter = getattr(canvas, "set_layer_blend_mode", None)
        if not callable(setter):
            return False
        setter(self.layer_id, self._old)
        self._executed = False
        return True

    def _restore_checked(self, canvas: Any, *, use_after: bool) -> bool:
        layer = find_layer(canvas, self.layer_id)
        if layer is None or self._checked_values is None or (not use_after and not self._executed):
            return False
        before, after = self._checked_values
        if self._submitting:
            # The checked submit already normalized/validated this scalar.
            # Publish no provisional redraw before its history is committed.
            layer.blend_mode = BlendMode(after if use_after else before)
            canvas._invalidate_layer_cache()
        else:
            canvas.set_layer_blend_mode(self.layer_id, after if use_after else before)
        self._old = before
        self._executed = use_after
        self._notify(canvas, layer)
        return True

    def _notify(self, canvas: Any, layer: Any) -> None:
        if not self._submitting:
            with contextlib.suppress(Exception):
                canvas._publish_layer_event(
                    Events.LAYER_CHANGED, layer=layer, change_type="property_changed"
                )


_BLEND_DEFAULT_METHODS = tuple(
    (name, getattr(SetLayerBlendModeCommand, name))
    for name in ("apply", "revert", "_notify", "_restore_checked")
)


def _is_standard_blend_command(command: Any) -> TypeGuard[SetLayerBlendModeCommand]:
    return type(command) is SetLayerBlendModeCommand and all(
        getattr_static(command, name, None) is method for name, method in _BLEND_DEFAULT_METHODS
    )
