"""Read persisted vector/text payloads and edit existing stable objects."""

from __future__ import annotations

from typing import Any

from loomstroke.plugins.vector_tools.common import denormalize_vector_object

from .editing_common import edit_operation, invalid
from .layer_edit_command import LayerEditCommand
from .types import McpToolSpec
from .vector_editing import ID, canvas_for, patch_object, replace_object, resolve_object, schema
from .vector_editing_schema import PATHS, STYLE

_TARGET = {"objectId": ID, "layerId": ID}
TOOLS = (
    McpToolSpec(
        "loomstroke_list_vectors",
        "List persisted vector and text payloads, including anchors and metadata.",
        schema({"layerId": ID}, editing=False),
    ),
    McpToolSpec(
        "loomstroke_get_vector",
        "Read a stable vector or text object, including paths, handles, "
        "style and saved text metadata.",
        schema(_TARGET, ["objectId"], editing=False),
    ),
    McpToolSpec(
        "loomstroke_update_vector",
        "Patch existing paths, style or metadata while preserving "
        "stable object and element identifiers.",
        schema(
            {
                **_TARGET,
                "patch": {
                    "type": "object",
                    "minProperties": 1,
                    "properties": {
                        "paths": PATHS,
                        "style": STYLE,
                        "meta": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            },
            ["objectId", "patch"],
        ),
    ),
    McpToolSpec(
        "loomstroke_delete_vector",
        "Delete one stable vector/text object with Undo/Redo and linked-story refresh.",
        schema(_TARGET, ["objectId"]),
    ),
)


async def dispatch(service: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    canvas = canvas_for(service)
    if name == "loomstroke_list_vectors":
        return {
            "objects": [
                {"layerId": layer_id, "object": denormalize_vector_object(obj)}
                for layer_id, obj in canvas.get_vector_objects(include_hidden=True)
                if args.get("layerId") in (None, layer_id)
            ]
        }
    layer_id, obj = resolve_object(canvas, args, editing=name != "loomstroke_get_vector")
    if name == "loomstroke_get_vector":
        return {"layerId": layer_id, "object": denormalize_vector_object(obj)}
    updated = patch_object(obj, args.get("patch")) if name == "loomstroke_update_vector" else None
    if name not in {"loomstroke_update_vector", "loomstroke_delete_vector"}:
        raise invalid("Unknown vector operation.")
    async with edit_operation(service, name, args) as canvas:
        layer_id, current = resolve_object(canvas, args, editing=True)
        if updated is not None:
            replace_object(canvas, layer_id, patch_object(current, args["patch"]))
        else:
            command = LayerEditCommand(
                [layer_id], lambda owner: _delete(owner, layer_id, str(obj.id))
            )
            if not canvas.submit(command, merge_key=None):
                raise invalid("The vector deletion was rejected.", "objectId")
        return {"changed": True, "undoable": True, "layerId": layer_id, "objectId": str(obj.id)}


def _delete(canvas: Any, layer_id: str, object_id: str) -> dict[str, Any]:
    objects = canvas._get_layer_by_id(layer_id).vector_objects
    index = next(i for i, obj in enumerate(objects) if str(obj.id) == object_id)
    if not canvas.remove_vector_object(layer_id, index, vec_id=object_id):
        raise invalid("The vector could not be removed.", "objectId")
    canvas._vector_selection = None
    return {}
