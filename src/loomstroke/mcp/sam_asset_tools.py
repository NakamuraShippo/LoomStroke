"""MCP schemas/dispatch for saved AI Selection asset processing."""

from __future__ import annotations

from typing import Any

from .sam_asset_common import ID_SCHEMA, schema
from .sam_mask_operations import OPTION_PROPERTIES, run_mask_operation
from .types import McpToolSpec

_DIMENSIONS = {
    "canvasWidth": {"type": "integer", "minimum": 1, "maximum": 16384},
    "canvasHeight": {"type": "integer", "minimum": 1, "maximum": 16384},
    "outputAssetId": ID_SCHEMA,
    "expectedRevision": {"type": "string", "minLength": 1},
}
TOOLS = (
    McpToolSpec(
        "loomstroke_ai_select_combine_masks",
        "Save a new union, subtract, intersect or XOR mask in canvas coordinates; "
        "keep inputs unchanged.",
        schema(
            {
                **_DIMENSIONS,
                "maskAssetIds": {
                    "type": "array",
                    "items": ID_SCHEMA,
                    "minItems": 2,
                    "maxItems": 32,
                },
                "mode": {"enum": ["union", "subtract", "intersect", "xor"]},
            },
            ["maskAssetIds", "mode"],
        ),
    ),
    McpToolSpec(
        "loomstroke_ai_select_refine_mask_asset",
        "Save a new locally refined mask: feather, expand, contract, smoothing, "
        "holes, islands, invert or threshold. No inference is started.",
        schema(
            {**_DIMENSIONS, "maskAssetId": ID_SCHEMA, "options": schema(OPTION_PROPERTIES)},
            ["maskAssetId", "options"],
        ),
    ),
)


async def dispatch(service: Any, name: str, arguments: Any) -> dict[str, Any]:
    return await run_mask_operation(service, name, arguments)
