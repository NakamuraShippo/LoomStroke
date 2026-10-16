"""Ordinary live filters and masks, separate from AI Effect tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.commands import DeleteLayerCommand, ModifyFilterParamsCommand
from loomstroke.core.filters import FilterRegistry
from loomstroke.core.mask_coverage import coverage_to_mask_image, normalize_mask_image

from .editing_common import current_revision, edit_operation, invalid
from .filter_arguments import parameter_schema, validate_params
from .filter_mask_command import FilterMaskCommand
from .layer_edit_command import LayerEditCommand
from .paint_extension_arguments import ID, action_schema, object_schema, validate
from .serialization import to_jsonable
from .types import McpStructuredError, McpToolSpec, ReasonCode

TOOLS = (
    McpToolSpec(
        "loomstroke_effect_catalog",
        "List every registered filter and its parameter schema/defaults.",
        object_schema({}),
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_manage_effect",
        "List/read, create, update parameters, or delete ordinary filter layers. Reuses "
        "registered filters and document Command history; stable filter IDs survive Undo/Redo. "
        "Parameters are strict and partial updates preserve other parameters.",
        action_schema(
            {
                "list": ({}, ()),
                "get": ({"effectId": ID}, ("effectId",)),
                "create": (
                    {
                        "effectType": ID,
                        "parentLayerId": {"type": ["string", "null"], "minLength": 1},
                        "params": {"type": "object"},
                    },
                    ("effectType",),
                ),
                "update": ({"effectId": ID, "params": {"type": "object"}}, ("effectId", "params")),
                "delete": ({"effectId": ID}, ("effectId",)),
            }
        ),
    ),
    McpToolSpec(
        "loomstroke_effect_mask",
        "Add/remove, enable, or replace an ordinary filter mask from the current selection "
        "or a registered canvas-sized PNG asset. Keeps painter/renderer masks synchronized "
        "and undoable; rejects unfinished mask strokes.",
        action_schema(
            {
                "add": ({"effectId": ID}, ("effectId",)),
                "remove": ({"effectId": ID}, ("effectId",)),
                "enable": (
                    {"effectId": ID, "enabled": {"type": "boolean"}},
                    ("effectId", "enabled"),
                ),
                "from_selection": ({"effectId": ID}, ("effectId",)),
                "from_asset": ({"effectId": ID, "assetId": ID}, ("effectId", "assetId")),
            }
        ),
    ),
)


def _layer(canvas: Any, filter_id: str, *, editable: bool = False) -> Any:
    layer = canvas._get_layer_by_id(filter_id)
    if layer is None or layer.layer_type != "effect":
        raise invalid("The requested ordinary filter layer does not exist.", "effectId")
    if editable:
        _unlocked(canvas, layer)
    return layer


def _unlocked(canvas: Any, layer: Any) -> None:
    while layer is not None:
        if layer.locked:
            raise McpStructuredError(
                ReasonCode.CONFLICT, "Unlock the target layer and its parents before editing."
            )
        layer = canvas._get_layer_by_id(layer.parent_id) if layer.parent_id else None


def _describe(layer: Any) -> dict[str, Any]:
    return {
        "id": layer.id,
        "name": layer.name,
        "type": layer.filter_type,
        "params": to_jsonable(layer.filter_params),
        "parentLayerId": layer.parent_id,
        "hasMask": layer.filter_mask is not None,
        "maskEnabled": layer.mask_enabled,
    }


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = validate(TOOLS, name, arguments)
    if name == "loomstroke_effect_catalog":
        return {
            "effects": [
                {
                    "type": type_id,
                    "name": label,
                    "category": category,
                    "parameterSchema": parameter_schema(type_id),
                }
                for type_id, label, category in FilterRegistry.list_filters()
            ]
        }
    canvas = service.canvas_service
    if canvas is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "An editable canvas is required."
        )
    action = args["action"]
    if action == "list":
        return {
            "effects": [
                _describe(layer)
                for layer in canvas._flatten_layers()
                if layer.layer_type == "effect"
            ]
        }
    if action == "get":
        return {"effect": _describe(_layer(canvas, args["effectId"]))}
    async with edit_operation(service, name, args) as canvas:
        if name == "loomstroke_effect_mask":
            return _mask(service, canvas, args)
        filter_id = args.get("effectId")
        if action == "create":
            params = validate_params(args["effectType"], args.get("params", {}))
            parent_id = args.get("parentLayerId")
            if parent_id is not None:
                parent = canvas._get_layer_by_id(parent_id)
                if parent is None or parent.layer_type in {"effect", "ai_effect"}:
                    raise invalid("An existing content/group parent is required.", "parentLayerId")
                _unlocked(canvas, parent)

            def create(target: Any) -> dict[str, Any]:
                result = target.add_filter_layer(parent_id, args["effectType"], params)
                if result is None:
                    raise McpStructuredError(
                        ReasonCode.BACKEND_FAILED, "The filter could not be created."
                    )
                return {"effectId": result}

            command = LayerEditCommand([], create)
            changed = canvas.submit(command)
            filter_id = command.result.get("effectId")
        elif action == "update":
            layer = _layer(canvas, filter_id, editable=True)
            patch = validate_params(layer.filter_type, args["params"])
            params = {**layer.filter_params, **patch}
            if params == layer.filter_params:
                return {
                    "status": "noop",
                    "effectId": filter_id,
                    "revision": current_revision(service),
                }
            changed = canvas.submit(ModifyFilterParamsCommand(filter_id, params))
        else:
            _layer(canvas, filter_id, editable=True)
            changed = canvas.submit(DeleteLayerCommand(filter_id))
        if not changed:
            raise McpStructuredError(ReasonCode.BACKEND_FAILED, "The filter operation failed.")
        return {"status": "applied", "effectId": filter_id, "revision": current_revision(service)}


def _mask(service: Any, canvas: Any, args: dict[str, Any]) -> dict[str, Any]:
    layer = _layer(canvas, args["effectId"], editable=True)
    if getattr(canvas, "_mask_paint_dirty", False):
        raise McpStructuredError(
            ReasonCode.CONFLICT, "Finish the current mask stroke before changing filter masks."
        )
    action = args["action"]
    mask = layer.filter_mask or layer.layer_mask
    enabled = layer.mask_enabled
    if action == "add":
        if mask is not None:
            raise invalid("This filter already has a mask.", "effectId")
        mask = QImage(canvas._width, canvas._height, QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(0xFFFFFFFF)
        enabled = True
    elif action == "remove":
        if mask is None:
            return {"status": "noop", "effectId": layer.id, "revision": current_revision(service)}
        mask = None
    elif action == "enable":
        if mask is None:
            raise invalid("This filter has no mask.", "effectId")
        enabled = args["enabled"]
        if enabled == layer.mask_enabled:
            return {"status": "noop", "effectId": layer.id, "revision": current_revision(service)}
    elif action == "from_selection":
        selected = canvas.get_selection_mask()
        if selected is None:
            raise invalid("An active selection is required.")
        mask = coverage_to_mask_image(selected.convertToFormat(QImage.Format.Format_Alpha8))
        enabled = True
    else:
        mask = _asset_mask(service, canvas, args["assetId"])
        enabled = True
    changed = canvas._simple_layer_command_submit().submit(
        FilterMaskCommand(layer.id, mask, enabled), "Filter Mask"
    )
    return {
        "status": "applied" if changed else "noop",
        "effectId": layer.id,
        "revision": current_revision(service),
    }


def _asset_mask(service: Any, canvas: Any, asset_id: str) -> QImage:
    payload = service._asset_payloads.get(asset_id)
    if not payload:
        raise invalid("A registered raster asset is required.", "assetId")
    mask = QImage.fromData(payload)
    if mask.isNull() or (mask.width(), mask.height()) != (canvas._width, canvas._height):
        raise invalid("The mask asset must match the canvas dimensions.", "assetId")
    mask = mask.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    normalize_mask_image(mask)
    return mask
