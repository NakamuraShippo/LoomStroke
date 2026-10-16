"""Document-wide Undo/Redo MCP tools using the existing history router."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .editing_common import check_gui_thread, current_revision, edit_operation
from .types import McpStructuredError, McpToolSpec, ReasonCode

_SCHEMA = {
    "type": "object",
    "properties": {"expectedRevision": {"type": "string", "minLength": 1}},
    "additionalProperties": False,
}
TOOLS = (
    McpToolSpec(
        "loomstroke_get_history_state",
        "Read document Undo/Redo availability.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_undo", "Undo one document history operation.", _SCHEMA, destructive=True
    ),
    McpToolSpec(
        "loomstroke_redo", "Redo one document history operation.", _SCHEMA, destructive=True
    ),
)


def _history(service: Any) -> tuple[Any, bool]:
    # Host history routes book and inactive-page entries; bypassing it would
    # undo a different operation from the application's document timeline.
    history = getattr(getattr(service, "host_api", None), "history", None)
    if history is not None:
        return history, True
    pages = getattr(service, "page_service", None)
    router = getattr(pages, "_document_history_router", None)
    if router is not None:
        return router, True
    canvas = getattr(service, "canvas_service", None)
    if canvas is not None and callable(getattr(canvas, "history_undo", None)):
        return canvas, False
    raise McpStructuredError(ReasonCode.CAPABILITY_UNAVAILABLE, "Document history is unavailable.")


def history_state(service: Any) -> dict[str, Any]:
    check_gui_thread()
    from .editing_readiness import document_state  # noqa: PLC0415

    if document_state(service).get("present") is not True:
        raise McpStructuredError(ReasonCode.CAPABILITY_UNAVAILABLE, "No editable document is open.")
    history, unified = _history(service)
    undo = history.can_undo if unified else history.history_can_undo
    redo = history.can_redo if unified else history.history_can_redo
    return {
        "schemaVersion": "mcp_history_state.v1",
        "canUndo": bool(undo()),
        "canRedo": bool(redo()),
        "scope": "document" if unified else "canvas",
        "contextDigest": current_revision(service),
    }


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if name == "loomstroke_get_history_state":
        return history_state(service)
    async with edit_operation(service, name, arguments):
        history, unified = _history(service)
        if name == "loomstroke_undo":
            operation = history.undo if unified else history.history_undo
        else:
            operation = history.redo if unified else history.history_redo
        changed = bool(operation())
        return {"changed": changed, "operation": name, **history_state(service)}
