"""Undoable layer opacity change (wraps the canvas' direct ``set_layer_opacity``)."""

from __future__ import annotations

import contextlib
from inspect import getattr_static
from typing import Any, TypeGuard

from .command_types import Command
from .commands_layer_lookup import find_layer
from .events import Events

__all__ = ["SetLayerOpacityCommand"]


class SetLayerOpacityCommand(Command):
    def __init__(self, layer_id: str, opacity: float, *, merge_key: str | None = None) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self._new = max(0.0, min(1.0, float(opacity)))
        self._old: float | None = None
        self._checked_values: tuple[float, float] | None = None
        self._submitting = False
        self._opacity_preview_owner: Any = None

    def apply(self, canvas: Any) -> bool:
        if self._checked_values is not None:
            return self._restore_checked(canvas, use_after=True)
        info = _layer_info(canvas, self.layer_id)
        setter = getattr(canvas, "set_layer_opacity", None)
        if info is None or not callable(setter):
            return False
        self._old = float(info.get("opacity", 1.0))
        setter(self.layer_id, self._new)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if self._checked_values is not None:
            return self._restore_checked(canvas, use_after=False)
        if not self._executed or self._old is None:
            return False
        setter = getattr(canvas, "set_layer_opacity", None)
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
        # Only the qualified Command route uses tree-aware scalar mutation;
        # the public root/balloon setter retains its existing lookup contract.
        layer.opacity = after if use_after else before
        canvas._invalidate_layer_cache()
        self._old, self._executed = before, use_after
        if not self._submitting:
            self._notify(canvas, layer)
            canvas._recompose()
        return True

    def _notify(self, canvas: Any, layer: Any) -> None:
        if not self._submitting:
            with contextlib.suppress(Exception):
                canvas._publish_layer_event(
                    Events.LAYER_CHANGED, layer=layer, change_type="property_changed"
                )


def _layer_info(canvas: Any, layer_id: str) -> dict[str, Any] | None:
    getter = getattr(canvas, "get_layer_info", None)
    if not callable(getter):
        return None
    info = getter(layer_id)
    return info if isinstance(info, dict) else None


_OPACITY_DEFAULT_METHODS = tuple(
    (name, getattr(SetLayerOpacityCommand, name))
    for name in ("apply", "revert", "_notify", "_restore_checked")
)


def _is_standard_opacity_command(command: Any) -> TypeGuard[SetLayerOpacityCommand]:
    return type(command) is SetLayerOpacityCommand and all(
        getattr_static(command, name, None) is method for name, method in _OPACITY_DEFAULT_METHODS
    )
