"""Bind one rename dialog result to its original canvas, page set, and layer."""

from __future__ import annotations

import logging
from typing import Any

from loomstroke.core.commands_layer_rename import RenameLayerCommand

logger = logging.getLogger(__name__)


def _page_set(pages: Any) -> Any:
    state = getattr(pages, "__dict__", None)
    return state.get("_pages") if isinstance(state, dict) else None


def _rename_layer_from_panel(panel: Any, layer_id: str, input_dialog: Any) -> None:
    host = panel._host_api
    canvas = panel._get_canvas()
    layer = panel._resolve_canvas_layer(layer_id)
    if canvas is None or layer is None:
        return
    document = getattr(canvas, "_doc", None)
    pages = getattr(host, "pages", None)
    page_set = _page_set(pages)
    current_name = str(getattr(layer, "name", ""))
    new_name, accepted = input_dialog.getText(
        panel, panel._ts("Rename Layer"), panel._ts("Layer name:"), text=current_name
    )
    if not accepted:
        return
    new_name = new_name.strip()
    if not new_name or new_name == current_name:
        return
    if (
        panel._host_api is not host
        or panel._get_canvas() is not canvas
        or getattr(canvas, "_doc", None) is not document
        or getattr(host, "pages", None) is not pages
        or _page_set(pages) is not page_set
        or panel._resolve_canvas_layer(layer_id) is not layer
    ):
        return
    submit = getattr(canvas, "submit", None)
    if not callable(submit):
        return
    try:
        if submit(RenameLayerCommand(layer_id, new_name)):
            panel._add_default_layers()
    except Exception:
        logger.debug("Layer rename submission failed", exc_info=True)
