"""MCP schemas and dispatch for detached source capture and candidate point refinement."""

from __future__ import annotations

from typing import Any

from .sam_asset_common import ID_SCHEMA, schema
from .sam_candidate_refinement import refine_candidate
from .sam_source_operations import SOURCE_SCOPES, create_source_snapshot
from .types import McpToolSpec

TOOLS = (
    McpToolSpec(
        "loomstroke_ai_select_create_source_snapshot",
        "Capture active layer, visible composite, selection bounds with padding, explicit page "
        "or registered imported asset. Selection bounds keep 1:1 canvas coordinates.",
        schema(
            {
                "scope": {"enum": list(SOURCE_SCOPES)},
                "layerId": {"type": "string", "minLength": 1, "maxLength": 256},
                "pageId": {"type": "string", "minLength": 1, "maxLength": 256},
                "assetId": {"type": "string", "minLength": 1, "maxLength": 256},
                "padding": {"type": "integer", "minimum": 0, "maximum": 512},
                "outputAssetId": ID_SCHEMA,
                "expectedRevision": {"type": "string", "minLength": 1},
            },
            ["scope"],
        ),
    ),
    McpToolSpec(
        "loomstroke_ai_select_refine_candidate",
        "Refine one saved candidate using positive/negative points on its saved source image; "
        "persist new candidate, mask and job without overwriting the base.",
        schema(
            {
                "candidateSetId": ID_SCHEMA,
                "candidateId": ID_SCHEMA,
                "points": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": schema(
                        {
                            "x": {"type": "number", "minimum": 0},
                            "y": {"type": "number", "minimum": 0},
                            "label": {"enum": ["positive", "negative"]},
                        },
                        ["x", "y"],
                    ),
                },
                "expectedRevision": {"type": "string", "minLength": 1},
            },
            ["candidateSetId", "candidateId", "points"],
        ),
    ),
)


async def dispatch(service: Any, name: str, arguments: Any) -> dict[str, Any]:
    if name.endswith("create_source_snapshot"):
        return await create_source_snapshot(service, name, arguments)
    return await refine_candidate(service, name, arguments)
