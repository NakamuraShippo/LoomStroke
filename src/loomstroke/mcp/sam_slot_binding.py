"""One undo step for existing SAM mask-to-AI Effect slot binding."""

from __future__ import annotations

from typing import Any

from loomstroke.ai_selection.ai_effect_slot_binding import build_ai_effect_mask_slot_binding_command

from .editing_common import edit_operation, invalid
from .layer_edit_command import LayerEditCommand
from .layer_operations import _editable, _layer
from .sam_asset_common import asset_error, dimensions, load, provider_store
from .types import McpStructuredError, ReasonCode


async def bind_slot(service: Any, name: str, args: Any) -> dict[str, Any]:
    _provider, store = provider_store(service)
    asset = load(store, "mask_asset", args.get("maskAssetId"), "maskAssetId")
    width, height = dimensions(service, {})
    async with edit_operation(service, name, args) as canvas:
        layer = _layer(canvas, args.get("layerId"))
        _editable(canvas, layer)
        state = getattr(layer, "ai_effect", None)
        if layer.layer_type != "ai_effect" or state is None:
            raise invalid("'layerId' must name an AI Effect layer.", "layerId")
        slot_id = args.get("slotId")
        if not isinstance(slot_id, str) or slot_id not in state.slot_refs:
            raise invalid("'slotId' must name an existing AI Effect input slot.", "slotId")
        excluded = args.get("excludeBlend", True)
        if not isinstance(excluded, bool):
            raise invalid("'excludeBlend' must be boolean.", "excludeBlend")
        try:
            adapter = build_ai_effect_mask_slot_binding_command(
                store,
                asset.asset_id,
                effect_layer_id=layer.id,
                slot_id=slot_id,
                canvas_width=width,
                canvas_height=height,
                exclude_blend=excluded,
            )

            def operation(owner: Any) -> dict[str, Any]:
                if not adapter.apply(owner):
                    raise McpStructuredError(
                        ReasonCode.BACKEND_FAILED, "Mask slot binding failed and was rolled back."
                    )
                command.target_ids.add(adapter.mask_layer_id)
                return {
                    "layerId": layer.id,
                    "slotId": slot_id,
                    "maskLayerId": adapter.mask_layer_id,
                }

            command = LayerEditCommand([layer.id], operation)
            if not canvas.submit(command):
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED, "Mask slot binding failed and was rolled back."
                )
        except (ValueError, OSError) as exc:
            raise asset_error(exc) from exc
    return {"status": "completed", **command.result, "canvasChanged": True, "undoable": True}
