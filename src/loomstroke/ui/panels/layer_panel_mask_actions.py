"""Continue mask-action UI only after success on the original editing scope."""

from __future__ import annotations

import logging
from typing import Any

from loomstroke.core.commands import AddLayerMaskCommand, RemoveLayerMaskCommand

logger = logging.getLogger(__name__)


def _stored(owner: Any, name: str) -> Any:
    state = getattr(owner, "__dict__", None)
    return state.get(name) if isinstance(state, dict) else None


def _active_state(page_set: Any, active_id: Any) -> Any:
    if not isinstance(page_set, list | tuple) or active_id is None:
        return None
    return next(
        (
            state
            for state in page_set
            if getattr(getattr(state, "page", None), "id", None) == active_id
        ),
        None,
    )


class _MaskActionScope:
    """Call-local references; no image copies, leases, or persistent registry."""

    def __init__(self, panel: Any, canvas: Any, layer: Any) -> None:
        self.panel, self.canvas, self.layer = panel, canvas, layer
        self.host = panel._host_api
        self.document = _stored(canvas, "_doc")
        self.pages = getattr(self.host, "pages", None)
        self.page_set = _stored(self.pages, "_pages")
        self.active_id = _stored(self.pages, "_active_page_id")
        self.state = _active_state(self.page_set, self.active_id)

    def current(self) -> bool:
        return (
            self.panel._host_api is self.host
            and self.panel._get_canvas() is self.canvas
            and _stored(self.canvas, "_doc") is self.document
            and getattr(self.host, "pages", None) is self.pages
            and _stored(self.pages, "_pages") is self.page_set
            and _stored(self.pages, "_active_page_id") == self.active_id
            and _active_state(self.page_set, self.active_id) is self.state
            and self.panel._resolve_canvas_layer(self.layer.id) is self.layer
        )


def _mask_action_from_panel(panel: Any, layer_id: str, *, add: bool) -> None:
    operation = "add" if add else "remove"
    try:
        canvas = panel._get_canvas()
        if canvas is None:
            return
        layer = panel._resolve_canvas_layer(layer_id)
        if layer is None:
            return
        scope = _MaskActionScope(panel, canvas, layer)
        if not scope.current():
            return
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            command = AddLayerMaskCommand(layer_id) if add else RemoveLayerMaskCommand(layer_id)
            success = bool(submit(command))
        else:
            legacy = getattr(canvas, f"{operation}_layer_mask", None)
            if not callable(legacy):
                return
            # Older backends may return None after accepting the operation.
            success = legacy(layer_id) is not False
        if not success or not scope.current():
            return
        if add:
            set_mode = getattr(canvas, "set_mask_edit_mode", None)
            if callable(set_mode):
                set_mode(layer_id, True)
        if scope.current():
            panel._add_default_layers()
    except Exception:
        logger.debug("%s_layer_mask failed for %s", operation, layer_id, exc_info=True)
    finally:
        panel._release_layer_op()
