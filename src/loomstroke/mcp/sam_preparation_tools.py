"""MCP schemas for local manga preparation, protection QA and mask input binding."""

from __future__ import annotations

from typing import Any

from .editing_common import invalid
from .sam_asset_common import ID_SCHEMA, schema
from .sam_manga_preparation import prepare_inpaint, prepare_text_protection, protection_qa
from .sam_slot_binding import bind_slot
from .types import McpToolSpec

_COMMON = {
    "canvasWidth": {"type": "integer", "minimum": 1, "maximum": 16384},
    "canvasHeight": {"type": "integer", "minimum": 1, "maximum": 16384},
    "expectedRevision": {"type": "string", "minLength": 1},
}
_CANDIDATE = {
    **_COMMON,
    "candidateSetId": ID_SCHEMA,
    "category": {"type": "string", "minLength": 1},
    "outputAssetId": ID_SCHEMA,
}
TOOLS = (
    McpToolSpec(
        "loomstroke_ai_select_prepare_text_protection",
        "Union explicitly accepted manga speech bubbles, captions and sound-effect text "
        "into a saved protection mask; retain acceptance metadata. No external generation.",
        schema(
            {
                **_CANDIDATE,
                "acceptedCandidateIds": {
                    "type": "array",
                    "items": ID_SCHEMA,
                    "maxItems": 32,
                    "uniqueItems": True,
                },
                "categoryByCandidateId": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "maxProperties": 32,
                },
            },
            ["candidateSetId", "category", "acceptedCandidateIds"],
        ),
    ),
    McpToolSpec(
        "loomstroke_ai_select_prepare_manga_inpaint",
        "Prepare an explicitly accepted candidate for inpaint by subtracting its protection "
        "mask into a new asset and running local overlap QA. No external generation.",
        schema(
            {
                **_CANDIDATE,
                "candidateId": ID_SCHEMA,
                "accepted": {"type": "boolean"},
                "protectedMaskAssetId": ID_SCHEMA,
            },
            ["candidateSetId", "candidateId", "category", "accepted", "protectedMaskAssetId"],
        ),
    ),
    McpToolSpec(
        "loomstroke_ai_select_analyze_protection",
        "Report local generation mask area, holes, canvas border and protected-region overlap, "
        "without inference or external generation.",
        schema(
            {**_COMMON, "maskAssetId": ID_SCHEMA, "protectedMaskAssetId": ID_SCHEMA},
            ["maskAssetId"],
        ),
    ),
    McpToolSpec(
        "loomstroke_ai_select_bind_mask_to_ai_effect_slot",
        "Materialize a saved SAM mask as a normal mask layer and bind it to an existing "
        "AI Effect input slot, with one undo. No external generation.",
        schema(
            {
                "maskAssetId": ID_SCHEMA,
                "layerId": ID_SCHEMA,
                "slotId": {"type": "string", "minLength": 1, "maxLength": 192},
                "excludeBlend": {"type": "boolean"},
                "expectedRevision": {"type": "string", "minLength": 1},
            },
            ["maskAssetId", "layerId", "slotId"],
        ),
    ),
)


async def dispatch(service: Any, name: str, arguments: Any) -> dict[str, Any]:
    if name.endswith("prepare_text_protection"):
        return await prepare_text_protection(service, name, arguments)
    if name.endswith("prepare_manga_inpaint"):
        return await prepare_inpaint(service, name, arguments)
    if name.endswith("analyze_protection"):
        return await protection_qa(service, arguments)
    if name.endswith("bind_mask_to_ai_effect_slot"):
        return await bind_slot(service, name, arguments)
    raise invalid("Unknown SAM preparation tool.")
