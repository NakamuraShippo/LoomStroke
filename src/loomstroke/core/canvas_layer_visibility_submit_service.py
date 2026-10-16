"""Layer visibility command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any, Protocol


class _LayerVisibilityCommand(Protocol):
    layer_id: str
    _old_visible: bool | None
    _new_visible: bool

    def apply(self, canvas: Any) -> bool: ...


class CanvasLayerVisibilitySubmitService:
    """Submit layer visibility commands and preserve canvas post-processing order."""

    def __init__(self, owner: Any, *, layer_changed_event: str) -> None:
        self._owner = owner
        self._layer_changed_event = layer_changed_event

    def submit(self, cmd: _LayerVisibilityCommand) -> bool:
        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        self._owner._record_history_command("Layer Visibility", cmd)
        self._owner._invalidate_layer_cache()
        self._owner._recompose()
        layer = self._owner._get_layer_by_id(cmd.layer_id)
        if layer is not None:
            self._owner._publish_layer_event(
                self._layer_changed_event,
                layer=layer,
                change_type="visibility",
                old_value=cmd._old_visible,
                new_value=cmd._new_visible,
            )
        return True
