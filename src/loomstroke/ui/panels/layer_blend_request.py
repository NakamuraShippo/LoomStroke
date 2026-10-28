"""Bind one synchronous panel blend request to its original model context."""

from __future__ import annotations

import logging
import weakref
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt

from loomstroke.core.commands_layer_blend_mode import SetLayerBlendModeCommand
from loomstroke.core.layer_command_commit_context import _LayerCommandContext

logger = logging.getLogger(__name__)
_SCOPE_KEY = "_blend_request_scope"


def _emit_panel_blend_request(panel: Any, index: int, mode: str, layer_id: str | None) -> None:
    host = panel._host_api
    events = getattr(host, "events", None)
    emit = getattr(events, "emit", None) or getattr(events, "publish", None)
    if not callable(emit):
        return
    canvas = panel._get_canvas()
    layer = panel._resolve_canvas_layer(layer_id) if layer_id is not None else None
    scope = _LayerCommandContext(canvas, (layer,)) if layer is not None else None
    payload = {"index": index, "mode": mode, "layer_id": layer_id}
    if scope is not None:
        if not scope.current():
            return
        # The EventBus may be observed or recorded. Only this synchronous call
        # owns the context; an old payload cannot retain its document or layers.
        payload[_SCOPE_KEY] = weakref.ref(scope)
    try:
        emit("layer.blend_mode_changed", payload)
    finally:
        if panel._host_api is host and panel._get_canvas() is canvas:
            if scope is not None and not scope.current():
                panel._add_default_layers()
            elif layer is not None:
                entry = panel._find_entry_by_id(layer_id)
                if entry is not None:
                    entry.blend_mode = str(getattr(layer.blend_mode, "value", layer.blend_mode))
                    current = panel._list.currentItem()
                    if (
                        current is not None
                        and current.data(0, Qt.ItemDataRole.UserRole) == layer_id
                    ):
                        panel._set_blend_mode(entry.blend_mode)


def _apply_panel_blend_request(canvas: Any, payload: Mapping[str, Any]) -> bool:
    mode, layer_id, index = payload.get("mode"), payload.get("layer_id"), payload.get("index")
    if mode is None:
        return False
    if _SCOPE_KEY in payload:
        reference = payload[_SCOPE_KEY]
        scope = reference() if isinstance(reference, weakref.ReferenceType) else None
        if (
            not isinstance(scope, _LayerCommandContext)
            or scope.canvas is not canvas
            or not scope.current()
            or len(scope.layers) != 1
            or scope.layers[0].id != layer_id
        ):
            return False
    if layer_id is None:
        layers = getattr(canvas, "_layers", [])
        if not isinstance(index, int) or not 0 <= index < len(layers):
            return False
        layer_id = layers[len(layers) - 1 - index].id
    try:
        submit = getattr(canvas, "submit", None)
        if callable(submit) and callable(getattr(canvas, "get_layer_info", None)):
            return bool(submit(SetLayerBlendModeCommand(layer_id, mode)))
        setter = getattr(canvas, "set_layer_blend_mode", None)
        if callable(setter):
            setter(layer_id, mode)
            return True
    except Exception:
        logger.exception("Layer blend request failed")
    return False
