"""Schemas and dispatch for guarded document lifecycle tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .document_operations import backend_failure, call_handler, document_handler
from .editing_common import check_gui_thread, edit_operation, finite_number, invalid
from .types import McpStructuredError, McpToolSpec, ReasonCode

_GUARD_PROPERTIES = {
    "discardChanges": {"type": "boolean", "default": False},
    "expectedRevision": {"type": "string", "minLength": 1},
}
TOOLS = (
    McpToolSpec(
        "loomstroke_new_document",
        "Create a document; unsaved changes require explicit discardChanges consent.",
        {
            "type": "object",
            "required": ["width", "height"],
            "properties": {
                **_GUARD_PROPERTIES,
                "width": {"type": "integer", "minimum": 1, "maximum": 16384},
                "height": {"type": "integer", "minimum": 1, "maximum": 16384},
                "dpi": {"type": "integer", "minimum": 1, "maximum": 1200, "default": 96},
                "backgroundColor": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 255},
                    "minItems": 4,
                    "maxItems": 4,
                    "default": [0, 0, 0, 0],
                },
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_open_document",
        "Open a validated .lstroke file under MCP roots; protect unsaved changes.",
        {
            "type": "object",
            "required": ["path"],
            "properties": {**_GUARD_PROPERTIES, "path": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_save_document",
        "Save the document; a path performs Save As, and existing files require overwrite=true.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "overwrite": {"type": "boolean", "default": False},
                "expectedRevision": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_close_document",
        "Close the current document; protect unsaved changes unless explicitly discarded.",
        {"type": "object", "properties": _GUARD_PROPERTIES, "additionalProperties": False},
        destructive=True,
    ),
)
_OPERATIONS = {
    tool.name: tool.name.removeprefix("loomstroke_").removesuffix("_document") for tool in TOOLS
}


def _integer(value: Any, field: str, maximum: int) -> int:
    number = finite_number(value, field, minimum=1, maximum=maximum)
    if not number.is_integer():
        raise invalid(f"'{field}' must be an integer.", field)
    return int(number)


def _arguments(
    service: Any, operation: str, arguments: Mapping[str, Any], state: Mapping[str, Any]
) -> dict[str, Any]:
    args = dict(arguments)
    for field in ("discardChanges", "overwrite"):
        if field in args and not isinstance(args[field], bool):
            raise invalid(f"'{field}' must be a boolean.", field)
    if operation == "new":
        args["width"] = _integer(args.get("width"), "width", 16384)
        args["height"] = _integer(args.get("height"), "height", 16384)
        args["dpi"] = _integer(args.get("dpi", 96), "dpi", 1200)
        rgba = args.get("backgroundColor", [0, 0, 0, 0])
        if not isinstance(rgba, list | tuple) or len(rgba) != 4:
            raise invalid("'backgroundColor' requires four byte values.", "backgroundColor")
        color = []
        for value in rgba:
            number = finite_number(value, "backgroundColor", minimum=0, maximum=255)
            if not number.is_integer():
                raise invalid("'backgroundColor' requires integer byte values.", "backgroundColor")
            color.append(int(number))
        args["backgroundColor"] = color
    if operation in {"open", "save"}:
        path = args.get("path") or (state.get("path") if operation == "save" else None)
        if not isinstance(path, str) or not path.strip():
            raise invalid("A non-empty document path is required.", "path")
        if operation == "open":
            resolved = service.safety.validate_input_path(path, allowed_suffixes=(".lstroke",))
        else:
            resolved = service.safety.validate_output_path(
                path, overwrite=args.get("overwrite", False), allowed_suffixes=(".lstroke",)
            )
        args["path"] = str(resolved)
    return args


def _check_state(operation: str, args: Mapping[str, Any], state: Mapping[str, Any]) -> None:
    if state.get("available") is not True:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "The document workflow is unavailable."
        )
    if state.get("saveInFlight"):
        raise McpStructuredError(ReasonCode.CONFLICT, "A document save is already in progress.")
    if (
        operation in {"new", "open", "close"}
        and state.get("dirty")
        and args.get("discardChanges") is not True
    ):
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "The document has unsaved changes; save it or explicitly request discardChanges.",
            details={"dirty": True, "discardChanges": False},
        )
    if operation == "save" and not state.get("present"):
        raise McpStructuredError(ReasonCode.NOT_FOUND, "No document is open.")


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    check_gui_thread()
    operation = _OPERATIONS[name]
    handler = document_handler(service)
    state = await call_handler(handler, "state", {})
    args = _arguments(service, operation, arguments, state)
    _check_state(operation, args, state)
    async with edit_operation(service, name, args, require_canvas=False):
        current = await call_handler(handler, "state", {})
        _check_state(operation, args, current)
        if current != state:
            raise McpStructuredError(
                ReasonCode.CONFLICT, "The document state changed during confirmation."
            )
        # Recheck roots and overwrite after confirmation and immediately before I/O.
        args = _arguments(service, operation, args, current)
        try:
            updated = await call_handler(handler, operation, args)
        except McpStructuredError:
            raise
        except Exception as exc:
            raise backend_failure(operation) from exc
    return {
        "schemaVersion": "document_operation_result.v1",
        "operation": operation,
        "success": True,
        "documentMutation": "none" if operation == "save" else "document_boundary",
        "document": updated,
    }
