"""Command-backed position, scale, rotation and flip of existing elements."""

from __future__ import annotations

import math
from typing import Any

from loomstroke.core.commands import TransformSelectionCommand
from loomstroke.plugins.vector_tools.common import VectorReplaceCommand, compute_vector_bounds

from .editing_common import edit_operation, invalid
from .layer_edit_command import LayerEditCommand
from .selection_editing import selection_payload
from .selection_pixel_command import SelectionPixelEditCommand
from .transform_editing import affine, layer_bounds, layer_command, parameters, transformed_object
from .types import McpStructuredError, McpToolSpec, ReasonCode
from .vector_editing import ID, canvas_for, editable_layer, resolve_object, schema

_PAIR = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
TOOLS = (
    McpToolSpec(
        "loomstroke_transform",
        "Move/resize/rotate raster layers, vector paths/text frames or selected pixels; "
        "flip raster pixels and vector paths. Text glyph mirror requires rasterization. "
        "Existing raster transforms cannot store shear. "
        "Groups transform descendants atomically. "
        "Position/size specify target values; translation/scale/rotation are relative. "
        "Selection duplicate leaves the original pixels.",
        schema(
            {
                "target": {"enum": ["layer", "vector", "selection"]},
                "layerId": ID,
                "objectId": ID,
                "position": _PAIR,
                "size": _PAIR,
                "translation": _PAIR,
                "scale": _PAIR,
                "pivot": _PAIR,
                "rotationDegrees": {"type": "number"},
                "flipHorizontal": {"type": "boolean"},
                "flipVertical": {"type": "boolean"},
                "duplicate": {"type": "boolean"},
                "maskMode": {"enum": ["soft", "binary"]},
                "keepStrokeWidth": {"type": "boolean"},
            },
            ["target"],
        ),
    ),
)


def _descendants(layer):
    return [layer, *(child for node in layer.children for child in _descendants(node))]


def _commands(canvas, args):
    if args["target"] == "vector":
        layer_id, obj = resolve_object(canvas, args, editing=True)
        return [layer_id], [
            VectorReplaceCommand(str(obj.id), transformed_object(obj, args), layer_id=layer_id)
        ]
    layer = editable_layer(canvas, args.get("layerId") or canvas.current_layer().id)
    nodes = _descendants(layer)
    if layer.layer_type == "group":
        boxes = []
        for node in nodes:
            if node.layer_type == "vector":
                boxes.extend(
                    box
                    for obj in node.vector_objects
                    if (box := compute_vector_bounds(obj, include_stroke=False)) is not None
                )
            elif node.layer_type == "raster" and node.image is not None:
                boxes.append(layer_bounds(node))
        if not boxes:
            return [], []
        bounds = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
        translation, scale, pivot, angle = parameters(args, bounds)
        args = {
            "translation": list(translation),
            "scale": list(scale),
            "pivot": list(pivot),
            "rotationDegrees": math.degrees(angle),
            "keepStrokeWidth": args.get("keepStrokeWidth", True),
        }
    commands = []
    for node in nodes:
        editable_layer(canvas, node.id)
        if node.layer_type == "group":
            continue
        if node.layer_type == "vector":
            commands.extend(
                VectorReplaceCommand(str(obj.id), transformed_object(obj, args), layer_id=node.id)
                for obj in node.vector_objects
            )
        elif node.layer_type == "raster" and node.image is not None:
            commands.append(layer_command(node, args))
        else:
            raise invalid("This layer does not support the requested 2D transform.", "layerId")
    return [node.id for node in nodes], commands


async def dispatch(service: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    canvas_for(service)
    target = args.get("target")
    if target not in {"layer", "vector", "selection"}:
        raise invalid("Unknown transform target.", "target")
    if target != "selection" and args.get("duplicate", False):
        raise invalid("'duplicate' is supported only for selection transforms.", "duplicate")
    async with edit_operation(service, name, args) as canvas:
        if target == "selection":
            payload = selection_payload(canvas, args.get("layerId"))
            x, y, width, height = payload.bounds
            translation, scale, pivot, angle = parameters(args, (x, y, x + width, y + height))
            # Selection command transforms payload-local coordinates.
            transform = affine(translation, scale, pivot, angle)
            transform.tx += transform.a * x + transform.b * y
            transform.ty += transform.c * x + transform.d * y
            command = TransformSelectionCommand(
                payload,
                transform,
                duplicate=args.get("duplicate", False),
                mask_mode=args.get("maskMode", "soft"),
                layer_id=payload.source_layer_id,
            )
            command = SelectionPixelEditCommand(payload.source_layer_id, command)
        else:
            ids, commands = _commands(canvas, args)
            if not commands:
                return {"changed": False, "undoable": False}
            command = LayerEditCommand(ids, lambda owner: _apply(owner, commands))
        changed = bool(canvas.submit(command, merge_key=None))
        if not changed:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "The transform failed and was rolled back."
            )
        return {"changed": changed, "undoable": changed}


def _apply(canvas, commands):
    for command in commands:
        if not command.apply(canvas):
            raise invalid("The transform was rejected and rolled back.")
    return {}
