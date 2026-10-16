"""General selection editing and the application's selection clipboard."""

from __future__ import annotations

import asyncio
from typing import Any

from loomstroke.core.commands import (
    ApplySelectionMaskCommand,
    CutSelectionCommand,
    PasteSelectionCommand,
    UpdateSelectionMaskCommand,
)
from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.plugins.basic_selection.rect_tool import BasicSelectionRectTool
from loomstroke.plugins.vector_tools.common import VectorCreateCommand

from .composite_snapshot import composite_image
from .editing_common import (
    check_gui_thread,
    current_revision,
    edit_operation,
    finite_number,
    invalid,
)
from .selection_editing import (
    image_array,
    modified_mask,
    reference_array,
    selection_payload,
    wand_mask,
)
from .selection_pixel_command import SelectionPixelEditCommand
from .types import McpStructuredError, McpToolSpec, ReasonCode
from .vector_editing import ID, canvas_for, editable_layer, schema

_ACTIONS = [
    "magic_wand",
    "import_mask",
    "invert",
    "expand",
    "contract",
    "feather",
    "copy",
    "cut",
    "paste",
    "vectorize",
    "clear",
]
TOOLS = (
    McpToolSpec(
        "loomstroke_get_selection",
        "Read selection bounds and clipboard availability.",
        schema({}, editing=False),
    ),
    McpToolSpec(
        "loomstroke_edit_selection",
        "Wand/mask selection, invert/expand/contract/feather, copy/cut/paste, clear, "
        "or convert bounds to an editable vector shape. Wand without layerId "
        "references the visible composite; clipboard uses the specified or active raster layer.",
        schema(
            {
                "action": {"enum": _ACTIONS},
                "layerId": ID,
                "assetId": ID,
                "operation": {"enum": ["replace", "add", "subtract", "intersect"]},
                "point": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "position": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "bounds": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "radius": {"type": "integer", "minimum": 0, "maximum": 256},
                "tolerance": {"type": "integer", "minimum": 0, "maximum": 255},
                "gapClose": {"type": "integer", "minimum": 0, "maximum": 64},
                "contiguous": {"type": "boolean"},
            },
            ["action"],
        ),
    ),
)


def _clipboard(service):
    host = service.host_api
    clipboard = getattr(host, "selection_clipboard", None)
    if clipboard is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "The application selection clipboard is unavailable."
        )
    return clipboard


def _bounds(canvas):
    bounds = canvas.get_selection_bounds()
    return [bounds.x(), bounds.y(), bounds.width(), bounds.height()] if bounds else None


async def dispatch(  # noqa: PLR0912, PLR0915 - existing selection command variants
    service: Any, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    canvas = canvas_for(service)
    if name == "loomstroke_get_selection":
        host = service.host_api
        clipboard = getattr(host, "selection_clipboard", None)
        return {
            "bounds": _bounds(canvas),
            "clipboardAvailable": clipboard is not None,
            "clipboardEmpty": clipboard is None or clipboard.is_empty(),
        }
    action = args.get("action")
    if action not in _ACTIONS:
        raise invalid("Unknown selection action.", "action")
    if action in {"magic_wand", "import_mask"}:
        for required in (["point"] if action == "magic_wand" else ["assetId"]):
            if required not in args:
                raise invalid(f"'{required}' is required for {action}.", required)
    async with edit_operation(service, name, args) as canvas:
        check_gui_thread()
        if action == "magic_wand":
            point = args["point"]
            if len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in point):
                raise invalid("Wand point requires two integer coordinates.", "point")
            tolerance = int(
                finite_number(args.get("tolerance", 32), "tolerance", minimum=0, maximum=255)
            )
            gap = int(finite_number(args.get("gapClose", 0), "gapClose", minimum=0, maximum=64))
            revision = current_revision(service)
            reference = (
                reference_array(canvas, args["layerId"])
                if "layerId" in args
                else image_array(await composite_image(service))
            )
            alpha = await asyncio.to_thread(
                wand_mask, reference, *point, tolerance, args.get("contiguous", True), gap
            )
            if (
                service.canvas_service is not canvas
                or current_revision(service) != revision
                or getattr(canvas, "_in_transaction", False)
            ):
                raise McpStructuredError(
                    ReasonCode.CONFLICT, "The document changed while preparing the selection."
                )
            command = ApplySelectionMaskCommand(alpha, args.get("operation", "replace"))
        elif action == "import_mask":
            alpha = mask_coverage_alpha8(service._qimage_from_asset(args["assetId"]))
            bounds = tuple(args["bounds"]) if "bounds" in args else None
            operation = args.get("operation", "replace")
            if bounds is None and (alpha.width(), alpha.height()) != (
                canvas._width,
                canvas._height,
            ):
                raise invalid(
                    "Mask dimensions must match the canvas, or provide placement bounds.", "bounds"
                )
            coverage = UpdateSelectionMaskCommand._mask_to_numpy(
                alpha, alpha.width(), alpha.height()
            )
            command = ApplySelectionMaskCommand(
                alpha if coverage.any() else None,
                (
                    operation
                    if coverage.any()
                    else ("replace" if operation in {"replace", "intersect"} else operation)
                ),
                bounds=bounds,
            )
        elif action in {"invert", "expand", "contract", "feather"}:
            radius = int(finite_number(args.get("radius", 0), "radius", minimum=0, maximum=256))
            if action != "invert" and radius == 0:
                raise invalid("A positive radius is required.", "radius")
            revision = current_revision(service)
            mask = canvas.get_selection_mask()
            alpha = UpdateSelectionMaskCommand._mask_to_numpy(mask, canvas._width, canvas._height)
            result = await asyncio.to_thread(modified_mask, alpha, action, radius)
            if (
                service.canvas_service is not canvas
                or current_revision(service) != revision
                or getattr(canvas, "_in_transaction", False)
            ):
                raise McpStructuredError(
                    ReasonCode.CONFLICT, "The document changed while preparing the selection."
                )
            command = ApplySelectionMaskCommand(result if result.any() else None, "replace")
        elif action == "clear":
            command = UpdateSelectionMaskCommand(None, "replace")
        elif action in {"copy", "cut"}:
            clipboard = _clipboard(service)
            payload = selection_payload(canvas, args.get("layerId"), editing=action == "cut")
            if action == "copy":
                clipboard.set(payload)
                return {
                    "changed": False,
                    "copied": True,
                    "bounds": list(payload.bounds),
                    "undoable": False,
                }
            command = CutSelectionCommand(payload.source_layer_id)
        elif action == "paste":
            clipboard = _clipboard(service)
            payload = clipboard.get()
            if payload is None:
                raise invalid("The selection clipboard is empty.", "clipboard")
            layer = editable_layer(canvas, args.get("layerId") or canvas.current_layer().id)
            if layer.layer_type != "raster":
                raise invalid("Paste requires a raster layer.", "layerId")
            command = PasteSelectionCommand(
                payload, tuple(args["position"]) if "position" in args else None, layer_id=layer.id
            )
        else:
            bounds = _bounds(canvas)
            if bounds is None:
                raise invalid("A selection is required for vectorization.", "selection")
            obj = BasicSelectionRectTool._selection_bounds_to_vector_shape(*map(float, bounds))
            layer_id = args.get("layerId")
            if layer_id is not None and editable_layer(canvas, layer_id).layer_type != "vector":
                raise invalid("Vectorization destination must be a vector layer.", "layerId")
            command = VectorCreateCommand(obj, layer_id=layer_id)
        if action in {"cut", "paste"}:
            command = SelectionPixelEditCommand(command.layer_id, command)
        changed = bool(canvas.submit(command, merge_key=None))
        if not changed and action in {"cut", "paste"}:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "The selection pixel edit failed and was rolled back."
            )
        if changed and action == "cut":
            clipboard.set(payload)
        return {
            "changed": changed,
            "undoable": changed,
            "bounds": _bounds(canvas),
            **({"objectId": obj.id} if action == "vectorize" and changed else {}),
        }
