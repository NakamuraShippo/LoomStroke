"""Discoverable comic style input and compatibility normalization for MCP clients."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .types import McpStructuredError, ReasonCode

COMIC_STYLE_SCHEMA: dict[str, Any] = {
    "type": ["object", "string"],
    "description": (
        'Prefer an object, e.g. {"description":"watercolor","readingDirection":"rtl"}. '
        "Plain text and JSON-encoded objects are also accepted. Description is metadata "
        "only; it does not run T2i or generate artwork."
    ),
    "properties": {
        "description": {"type": "string", "description": "Art direction metadata only."},
        "readingDirection": {"type": "string", "description": "Reading direction metadata."},
        "fontFamily": {"type": "string"},
        "textColor": {"type": "string"},
        "panelStrokeColor": {"type": "string"},
        "panelStrokeWidth": {"type": "number"},
    },
    "additionalProperties": True,
}

COMIC_ART_GUIDANCE = (
    "This tool does not generate artwork or run T2i. Supply registered image assetIds; "
    "without them, art slots are placeholders. For AI Effect T2i, first discover installed "
    "presets with loomstroke_list_ai_effect_presets, apply the selected preset with "
    "loomstroke_apply_ai_effect_preset, and inspect loomstroke_get_ai_effect_layer_state "
    "for its actual parameter schema before setting prompts and generating. "
    "Do not put an image generation prompt in style expecting it to generate an image."
)


def normalize_comic_style(value: Any) -> dict[str, Any]:
    """Retain object inputs and recover the two common LLM string forms."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            decoded = None
        if isinstance(decoded, dict):
            return decoded
        return {"description": value}
    raise McpStructuredError(
        ReasonCode.INVALID_ARGUMENT,
        "'style' must be an object or a string.",
        details={"argument": "style", "expectedTypes": ["object", "string"]},
        code=-32602,
    )
