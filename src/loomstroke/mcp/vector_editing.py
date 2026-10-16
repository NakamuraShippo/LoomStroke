"""Shared stable-object resolution and command-backed vector editing."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from loomstroke.core.layer_tree_service import LayerTreeQueryService
from loomstroke.plugins.vector_tools.common import (
    VectorObject,
    VectorReplaceCommand,
    denormalize_vector_object,
    ensure_vector_element_ids,
    normalize_vector_object,
    validate_vector_object,
)

from .editing_common import check_gui_thread, invalid
from .types import McpStructuredError, ReasonCode

ID = {"type": "string", "minLength": 1}


def schema(properties: dict[str, Any], required=(), *, editing=True) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {**properties, **({"expectedRevision": ID} if editing else {})},
        "required": list(required),
        "additionalProperties": False,
    }


def canvas_for(service: Any) -> Any:
    from .editing_readiness import known_document_closed  # noqa: PLC0415 - avoid registration cycle

    check_gui_thread()
    canvas = service.canvas_service
    if canvas is None or known_document_closed(service):
        raise McpStructuredError(ReasonCode.CAPABILITY_UNAVAILABLE, "No document is open.")
    return canvas


def editable_layer(canvas: Any, layer_id: str) -> Any:
    query = LayerTreeQueryService(canvas)
    layer = query.find_layer_by_id_tree(layer_id)
    if layer is None:
        raise invalid("The layer does not exist.", "layerId")
    current = layer
    while current is not None:
        if current.locked:
            raise invalid("The layer or an ancestor is locked.", "layerId")
        current = query.find_layer_by_id_tree(current.parent_id) if current.parent_id else None
    return layer


def resolve_object(
    canvas: Any, args: Mapping[str, Any], *, editing=False
) -> tuple[str, VectorObject]:
    object_id = args.get("objectId")
    if not isinstance(object_id, str) or not object_id:
        raise invalid("A stable objectId is required.", "objectId")
    matches = [
        (layer_id, obj)
        for layer_id, obj in canvas.get_vector_objects(include_hidden=True)
        if str(getattr(obj, "id", "")) == object_id
        and (args.get("layerId") is None or layer_id == args["layerId"])
    ]
    if len(matches) != 1:
        raise invalid("The object is missing or ambiguous; specify layerId.", "objectId")
    layer_id, raw = matches[0]
    if editing:
        editable_layer(canvas, layer_id)
    obj = normalize_vector_object(raw)
    if obj is None:
        raise invalid("The vector payload cannot be decoded.", "objectId")
    return layer_id, ensure_vector_element_ids(copy.deepcopy(obj))


def checked_object(payload: Mapping[str, Any]) -> VectorObject:
    try:
        json.dumps(payload, allow_nan=False)
        obj = normalize_vector_object(copy.deepcopy(dict(payload)))
        if obj is None or validate_vector_object(obj):
            raise ValueError("Invalid vector geometry or style.")
        return ensure_vector_element_ids(obj)
    except (TypeError, ValueError, OverflowError) as exc:
        raise invalid(str(exc), "object") from exc


def patch_object(obj: VectorObject, patch: Any) -> VectorObject:
    if not isinstance(patch, Mapping) or not patch or set(patch) - {"paths", "style", "meta"}:
        raise invalid(
            "Patch must contain paths, style, or meta, preserving the stable id.", "patch"
        )
    payload = denormalize_vector_object(obj)
    for key, value in patch.items():
        if key in {"style", "meta"}:
            if not isinstance(value, Mapping):
                raise invalid(f"'{key}' must be an object.", "patch")
            if (
                key == "meta"
                and "loomstroke:ids" in value
                and value["loomstroke:ids"] != payload.get("meta", {}).get("loomstroke:ids")
            ):
                raise invalid("Stable element identifiers cannot be changed.", "patch")
            payload[key] = {**payload.get(key, {}), **copy.deepcopy(dict(value))}
        else:
            if not isinstance(value, list) or not value:
                raise invalid("Paths must be a nonempty array.", "patch")
            payload[key] = copy.deepcopy(value)
    return checked_object(payload)


def replace_object(canvas: Any, layer_id: str, obj: VectorObject) -> bool:
    command = VectorReplaceCommand(str(obj.id), ensure_vector_element_ids(obj), layer_id=layer_id)
    if not canvas.submit(command, merge_key=None):
        raise McpStructuredError(ReasonCode.BACKEND_FAILED, "The vector edit was rejected.")
    return True
