"""MCP schemas for bounded native video image-sequence tracking and mask export."""

from __future__ import annotations

from typing import Any

from .editing_common import invalid
from .sam_asset_common import ID_SCHEMA, schema
from .sam_video_operations import export_video, track_video
from .types import McpToolSpec

TOOLS = (
    McpToolSpec(
        "loomstroke_sam31_track_video",
        "Track a text concept in 1-32 ordered registered image frames (same size, total 64 MP). "
        "Persist per-frame masks and a job. Explicitly starts/loads the local worker if needed; "
        "closes its private video session.",
        schema(
            {
                "frameAssetIds": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 256},
                    "minItems": 1,
                    "maxItems": 32,
                },
                "prompt": {"type": "string", "minLength": 1, "maxLength": 2048},
                "fps": {"type": "number", "minimum": 0.01, "maximum": 240},
            },
            ["frameAssetIds", "prompt"],
        ),
    ),
    McpToolSpec(
        "loomstroke_sam31_export_video_masks",
        "Export a completed native video mask job as the existing mask package, QA and lineage "
        "JSON plus frame PNGs. Requires a new directory within MCP roots; preserves canvas.",
        schema(
            {"jobId": ID_SCHEMA, "outputDirectory": {"type": "string", "minLength": 1}},
            ["jobId", "outputDirectory"],
        ),
    ),
)


async def dispatch(service: Any, name: str, arguments: Any) -> dict[str, Any]:
    if name.endswith("track_video"):
        return await track_video(service, name, arguments)
    if name.endswith("export_video_masks"):
        return await export_video(service, name, arguments)
    raise invalid("Unknown SAM31 video tool.")
