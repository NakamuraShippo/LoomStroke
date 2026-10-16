"""MCP layer properties, tree editing, masks, and content operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .editing_common import check_gui_thread, edit_operation, invalid
from .layer_edit_command import LayerEditCommand
from .layer_operations import MASK_ACTIONS, PROPERTY_FIELDS, perform, validate
from .types import McpStructuredError, McpToolSpec, ReasonCode

_ID = {"type": "string", "minLength": 1}
_REVISION = {"expectedRevision": dict(_ID)}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {**properties, **_REVISION},
        "required": required,
        "additionalProperties": False,
    }


def _layer_schema(
    properties: dict[str, Any] | None = None, required: list[str] | None = None
) -> dict[str, Any]:
    return _schema({"layerId": dict(_ID), **(properties or {})}, ["layerId", *(required or [])])


TOOLS = (
    McpToolSpec(
        "loomstroke_update_layer",
        "Set active layer, visibility, lock, alpha lock, or clipping as one undoable edit. "
        "Use the existing changeset operations for name, opacity, and blend mode.",
        _layer_schema(
            {
                "properties": {
                    "type": "object",
                    "minProperties": 1,
                    "properties": {
                        **{key: {"type": "boolean"} for key in PROPERTY_FIELDS},
                        "active": {"const": True},
                    },
                    "additionalProperties": False,
                }
            },
            ["properties"],
        ),
    ),
    McpToolSpec(
        "loomstroke_move_layer",
        "Move a layer to a final zero-based sibling position, optionally into a group. "
        "Omit parentId to retain its parent; null moves to the root. Background stays first.",
        _layer_schema(
            {
                "position": {"type": "integer", "minimum": 0},
                "parentId": {"type": ["string", "null"], "minLength": 1},
            },
            ["position"],
        ),
    ),
    McpToolSpec(
        "loomstroke_group_layers",
        "Group at least two sibling layers in their existing order. "
        "Undo/redo retains the group ID.",
        _schema(
            {
                "layerIds": {
                    "type": "array",
                    "items": dict(_ID),
                    "minItems": 2,
                    "uniqueItems": True,
                },
                "name": dict(_ID),
            },
            ["layerIds"],
        ),
    ),
    McpToolSpec(
        "loomstroke_ungroup_layer",
        "Dissolve a group, preserving child order and undoable group identity.",
        _layer_schema(),
    ),
    McpToolSpec(
        "loomstroke_set_layer_mask",
        "Add/remove an attached mask, or derive it from the selection or a registered "
        "canvas-size raster asset. Enable/disable the mask or its edit mode. "
        "Uses canonical grayscale coverage; preserves selection.",
        _layer_schema(
            {
                "action": {"type": "string", "enum": sorted(MASK_ACTIONS)},
                "assetId": dict(_ID),
                "enabled": {"type": "boolean"},
            },
            ["action"],
        ),
    ),
    McpToolSpec(
        "loomstroke_delete_layer",
        "Delete a layer and its subtree, with undo. Background is protected.",
        _layer_schema(),
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_clear_layer",
        "Clear raster/vector/mask content, retaining layer properties and undo.",
        _layer_schema(),
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_merge_layers",
        "Merge down onto the immediately preceding sibling (retains destination ID), "
        "or stamp visible layers into a new raster layer while preserving originals. "
        "Undo restores masks, vector objects, and metadata.",
        _schema(
            {
                "mode": {"type": "string", "enum": ["down", "visible"], "default": "down"},
                "layerId": dict(_ID),
                "targetLayerId": dict(_ID),
            },
            [],
        ),
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_rasterize_layer",
        "Rasterize a vector/text layer using the existing renderer, with undo.",
        _layer_schema(),
        destructive=True,
    ),
)


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return raw structured content; central dispatch owns protocol wrapping."""
    check_gui_thread()
    if name not in {tool.name for tool in TOOLS}:
        raise invalid("Unknown layer editing tool.")
    kind = name.removeprefix("loomstroke_")
    canvas = getattr(service, "canvas_service", None)
    if canvas is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "An editable canvas is required."
        )
    mask_image = None
    if kind == "set_layer_mask" and arguments.get("action") == "from_asset":
        asset_id = arguments.get("assetId")
        if not isinstance(asset_id, str) or not asset_id:
            raise invalid("'assetId' must be a non-empty string.", "assetId")
        mask_image = service._qimage_from_asset(asset_id)
    validate(canvas, kind, arguments, mask_image)
    async with edit_operation(service, name, arguments) as canvas:
        targets = validate(canvas, kind, arguments, mask_image)
        command = LayerEditCommand(
            targets, lambda owner: perform(owner, kind, arguments, mask_image)
        )
        if not canvas.submit(command):
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "The layer edit failed and was rolled back."
            )
        return {
            "changed": True,
            **command.result,
            "undoable": True,
            "activeLayerId": canvas.current_layer().id,
        }
