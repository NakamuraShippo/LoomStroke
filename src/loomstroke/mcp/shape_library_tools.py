"""Existing Shape Manager library and editable basic-shape operations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from loomstroke.plugins.vector_tools.common import (
    VectorCreateCommand,
    VectorStyle,
    compute_vector_bounds,
    make_vector_object,
)
from loomstroke.plugins.vector_tools.parametric_shapes import (
    PARAMETRIC_META_KEY,
    build_parametric_shape_object,
    convert_parametric_to_vector,
    list_basic_shape_definitions,
)

from .editing_common import edit_operation, finite_number, invalid
from .extension_services import require_service
from .shape_library_repository import register_shape
from .transform_editing import transformed_object
from .types import McpStructuredError, McpToolSpec, ReasonCode
from .vector_editing import ID, canvas_for, editable_layer, replace_object, resolve_object, schema

_BOX = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}
TOOLS = (
    McpToolSpec(
        "loomstroke_list_shapes",
        "Search the Shape Manager library and basic parametric definitions "
        "by query, category, or tags.",
        schema(
            {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            editing=False,
        ),
    ),
    McpToolSpec(
        "loomstroke_register_shape",
        "Register an existing vector/text object in the persistent "
        "GUI Shape Library with a name, category and tags.",
        schema(
            {
                "objectId": ID,
                "layerId": ID,
                "name": ID,
                "category": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            ["objectId", "name"],
        ),
    ),
    McpToolSpec(
        "loomstroke_place_shape",
        "Place a stored library template or basic parametric shape in a vector layer. "
        "Bbox is [left, top, right, bottom]; params use the existing factory rules.",
        schema(
            {
                "shapeId": ID,
                "kind": {"enum": ["library", "basic"]},
                "layerId": ID,
                "bbox": _BOX,
                "params": {"type": "object", "additionalProperties": {"type": "number"}},
            },
            ["shapeId", "bbox"],
        ),
    ),
    McpToolSpec(
        "loomstroke_update_parametric_shape",
        "Edit basic shape parameters or convert to a regular vector "
        "while preserving stable identifiers and Undo/Redo.",
        schema(
            {
                "objectId": ID,
                "layerId": ID,
                "params": {"type": "object", "additionalProperties": {"type": "number"}},
                "bbox": _BOX,
                "convertToVector": {"type": "boolean"},
            },
            ["objectId"],
        ),
    ),
)


def _bbox(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise invalid("bbox must contain left, top, right, bottom.", "bbox")
    box = tuple(finite_number(value, "bbox", minimum=-1e6, maximum=1e6) for value in raw)
    if box[2] <= box[0] or box[3] <= box[1]:
        raise invalid("bbox must have positive dimensions.", "bbox")
    return box


def _params(raw, supported):
    if not isinstance(raw, dict) or set(raw) - set(supported):
        raise invalid("The shape has no such parameter.", "params")
    return {key: finite_number(value, key) for key, value in raw.items()}


def _placed(panel, record, box):
    template = panel._load_template(record)
    if template is None:
        raise invalid("The shape template cannot be read.", "shapeId")
    x0, y0, x1, y1 = template.bbox
    if x1 <= x0 or y1 <= y0:
        raise invalid("The shape template has no bounds.", "shapeId")
    meta = deepcopy(dict(template.meta))
    meta.pop("loomstroke:ids", None)
    obj = make_vector_object(list(template.paths), template.style, meta=meta)
    return transformed_object(
        obj,
        {
            "position": [box[0], box[1]],
            "size": [box[2] - box[0], box[3] - box[1]],
            "pivot": [x0, y0],
        },
    )


def _updated_shape(obj, args):
    payload = obj.meta.get(PARAMETRIC_META_KEY)
    if not isinstance(payload, dict):
        raise invalid("The object is not a parametric shape.", "objectId")
    if args.get("convertToVector", False):
        return convert_parametric_to_vector(obj)
    box = _bbox(
        args.get(
            "bbox",
            payload.get("reference_bbox") or compute_vector_bounds(obj, include_stroke=False),
        )
    )
    params = {
        **payload.get("params", {}),
        **_params(args.get("params", {}), payload.get("params", {})),
    }
    meta = {key: deepcopy(value) for key, value in obj.meta.items() if key != PARAMETRIC_META_KEY}
    updated = build_parametric_shape_object(
        payload["shape_id"],
        box,
        params,
        style=obj.style,
        object_id=str(obj.id),
        extra_meta=meta,
    )
    if updated is None:
        raise invalid("The shape edit produced no geometry.", "params")
    return updated


async def dispatch(  # noqa: PLR0912, PLR0915 - four Shape Manager operations
    service: Any, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    canvas = canvas_for(service)
    if name == "loomstroke_list_shapes":
        panel = require_service(service, "shape_manager")
        records = [record.to_dict() for record in panel._index.all()]
        query = args.get("query", "").casefold()
        category = args.get("category")
        tags = set(args.get("tags", []))
        records = [
            record
            for record in records
            if (
                not query
                or query in record["name"].casefold()
                or query in " ".join(record["tags"]).casefold()
            )
            and (category is None or record["category"] == category)
            and tags <= set(record["tags"])
        ]
        basic = [
            asdict(definition)
            for definition in list_basic_shape_definitions()
            if (
                not query
                or query in definition.display_name.casefold()
                or query in definition.shape_id.casefold()
            )
            and (category is None or definition.category == category)
        ]
        return {"library": records, "basic": basic}
    if name == "loomstroke_update_parametric_shape":
        _, obj = resolve_object(canvas, args, editing=True)
        _updated_shape(obj, args)
    async with edit_operation(service, name, args) as canvas:
        if name == "loomstroke_update_parametric_shape":
            layer_id, current = resolve_object(canvas, args, editing=True)
            replace_object(canvas, layer_id, _updated_shape(current, args))
            return {"changed": True, "undoable": True, "objectId": str(current.id)}
        if name == "loomstroke_register_shape":
            panel = require_service(service, "shape_manager")
            _, obj = resolve_object(canvas, args)
            record = register_shape(panel, obj, args)
            return {"registered": True, "record": record.to_dict(), "undoable": False}
        if name != "loomstroke_place_shape":
            raise invalid("Unknown Shape Library operation.")
        box = _bbox(args["bbox"])
        if args.get("kind", "library") == "basic":
            default = build_parametric_shape_object(args["shapeId"], box, style=VectorStyle())
            if default is None:
                raise invalid("Unknown basic shape.", "shapeId")
            params = _params(args.get("params", {}), default.meta[PARAMETRIC_META_KEY]["params"])
            obj = build_parametric_shape_object(args["shapeId"], box, params, style=default.style)
        else:
            panel = require_service(service, "shape_manager")
            record = panel._index.get(args["shapeId"])
            if record is None:
                raise invalid("The library shape does not exist.", "shapeId")
            obj = _placed(panel, record, box)
        layer_id = args.get("layerId")
        if layer_id is not None and editable_layer(canvas, layer_id).layer_type != "vector":
            raise invalid("Shape placement requires a vector layer.", "layerId")
        command = VectorCreateCommand(obj, layer_id=layer_id)
        changed = bool(canvas.submit(command, merge_key=None))
        if not changed:
            raise McpStructuredError(ReasonCode.BACKEND_FAILED, "The shape placement was rejected.")
        inserted_layer_id, _ = resolve_object(canvas, {"objectId": str(obj.id)})
        return {
            "changed": changed,
            "undoable": changed,
            "objectId": obj.id,
            "layerId": inserted_layer_id,
        }
