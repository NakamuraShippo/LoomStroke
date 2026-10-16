"""MCP schemas for existing SAM layer conversion commands."""

from __future__ import annotations

from typing import Any

from .editing_common import invalid
from .sam_asset_common import ID_SCHEMA, schema
from .sam_layer_operations import apply_layer_adapter
from .types import McpToolSpec

_COMMON = {"expectedRevision": {"type": "string", "minLength": 1}}
_SOURCE = {
    **_COMMON,
    "sourceLayerId": ID_SCHEMA,
    "name": {"type": "string", "minLength": 1, "maxLength": 256},
}
TOOLS = (
    McpToolSpec(
        "loomstroke_ai_select_extract_to_layer",
        "Extract foreground or background from a raster layer into a new layer, "
        "preserving the original, with one undo.",
        schema(
            {**_SOURCE, "maskAssetId": ID_SCHEMA, "mode": {"enum": ["foreground", "background"]}},
            ["sourceLayerId", "maskAssetId"],
        ),
    ),
    McpToolSpec(
        "loomstroke_ai_select_apply_clipping_mask",
        "Apply a saved SAM mask and enable clipping on a raster layer, with one undo.",
        schema(
            {
                **_COMMON,
                "layerId": ID_SCHEMA,
                "maskAssetId": ID_SCHEMA,
                "maskEnabled": {"type": "boolean"},
            },
            ["layerId", "maskAssetId"],
        ),
    ),
    McpToolSpec(
        "loomstroke_ai_select_create_candidate_group",
        "Extract 2-32 saved candidates from a raster layer into one group, "
        "preserving source pixels, with one undo.",
        schema(
            {
                **_SOURCE,
                "candidateSetId": ID_SCHEMA,
                "candidateIds": {
                    "type": "array",
                    "items": ID_SCHEMA,
                    "minItems": 2,
                    "maxItems": 32,
                    "uniqueItems": True,
                },
            },
            ["sourceLayerId", "candidateSetId", "candidateIds"],
        ),
    ),
)


async def dispatch(service: Any, name: str, arguments: Any) -> dict[str, Any]:
    if name not in {tool.name for tool in TOOLS}:
        raise invalid("Unknown SAM layer editing tool.")
    return await apply_layer_adapter(service, name, arguments)
