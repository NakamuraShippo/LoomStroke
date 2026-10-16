"""MCP schemas for PNG/WebP cutouts and deterministic sprite-atlas export."""

from __future__ import annotations

from typing import Any

from .editing_common import invalid
from .sam_asset_common import ID_SCHEMA, schema
from .sam_export_operations import export_assets
from .types import McpToolSpec

_COMMON = {
    "sourceLayerId": ID_SCHEMA,
    "outputPath": {"type": "string", "minLength": 1},
    "padding": {"type": "integer", "minimum": 0, "maximum": 64},
    "expectedRevision": {"type": "string", "minLength": 1},
}
TOOLS = (
    McpToolSpec(
        "loomstroke_ai_select_export_cutout",
        "Export a transparent PNG/WebP cutout with layer-local alpha bbox trimming "
        "and optional padding. Preserve canvas; output must be new and within MCP roots.",
        schema(
            {**_COMMON, "maskAssetId": ID_SCHEMA}, ["sourceLayerId", "maskAssetId", "outputPath"]
        ),
    ),
    McpToolSpec(
        "loomstroke_ai_select_export_sprite_atlas",
        "Export 1-64 transparent cutouts as a PNG/WebP sprite sheet and matching JSON atlas. "
        "Support bbox padding, spacing and power-of-two sizing; outputs must be new "
        "and within MCP roots.",
        schema(
            {
                **_COMMON,
                "maskAssetIds": {
                    "type": "array",
                    "items": ID_SCHEMA,
                    "minItems": 1,
                    "maxItems": 64,
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 192},
                    "maxItems": 64,
                    "uniqueItems": True,
                },
                "spacing": {"type": "integer", "minimum": 0, "maximum": 64},
                "maxSide": {"type": "integer", "minimum": 1, "maximum": 8192},
                "powerOfTwo": {"type": "boolean"},
            },
            ["sourceLayerId", "maskAssetIds", "outputPath"],
        ),
    ),
)


async def dispatch(service: Any, name: str, arguments: Any) -> dict[str, Any]:
    if name not in {tool.name for tool in TOOLS}:
        raise invalid("Unknown SAM export tool.")
    return await export_assets(service, name, arguments)
