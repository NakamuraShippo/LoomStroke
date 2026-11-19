"""Client-discoverable brush argument schemas and call examples."""

from __future__ import annotations

import json
from typing import Any

from . import brush_settings
from .brush_stroke import (
    MAX_BATCH_POINTS,
    MAX_BATCH_REGION_PIXELS,
    MAX_POINTS,
    MAX_STROKE_REGION_PIXELS,
    MAX_STROKES,
)

TOOL_ID = {"type": "string", "enum": list(brush_settings.BRUSH_IDS)}
SETTING_PROPERTIES = {
    key: {"type": "number", "minimum": bounds[0], "maximum": bounds[1]}
    for key, bounds in brush_settings.NUMBER_LIMITS.items()
}
SETTING_PROPERTIES.update({key: {"type": "boolean"} for key in brush_settings.BOOLEAN_KEYS})
SETTING_PROPERTIES["color"] = {
    "description": "Pen color as #RRGGBB, #RRGGBBAA, or an RGB/RGBA array of 8-bit channels.",
    "oneOf": [
        {"type": "string", "pattern": "^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$"},
        {
            "type": "array",
            "minItems": 3,
            "maxItems": 4,
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
        },
    ],
}
SETTINGS = {"type": "object", "additionalProperties": False, "properties": SETTING_PROPERTIES}
POINT = {
    "type": "object",
    "required": ["x", "y"],
    "additionalProperties": False,
    "properties": {
        "x": {"type": "number", "minimum": 0, "description": "Canvas x coordinate in pixels."},
        "y": {"type": "number", "minimum": 0, "description": "Canvas y coordinate in pixels."},
        "pressure": {"type": "number", "minimum": 0, "maximum": 1, "default": 1},
        "tilt_x": {"type": "number", "minimum": -60, "maximum": 60},
        "tilt_y": {"type": "number", "minimum": -60, "maximum": 60},
        "time": {"type": "number", "minimum": 0, "maximum": 1e15},
    },
}
_LAYER_ID = {
    "type": "string",
    "minLength": 1,
    "maxLength": 256,
    "description": "Target raster layer ID. Find the active ID with loomstroke_get_edit_context.",
}
_POINTS = {
    "type": "array",
    "minItems": 1,
    "maxItems": MAX_POINTS,
    "items": POINT,
    "description": "Ordered points inside the canvas. Supply one point for a dot.",
}


def schema(
    properties: dict[str, Any], required: list[str] | None = None, *, edit: bool = False
) -> dict[str, Any]:
    if edit:
        properties = {**properties, "expectedRevision": {"type": "string", "minLength": 1}}
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _stroke_variants(*, standalone: bool) -> dict[str, Any]:
    # Explicit variants let MCP clients project the tool ID and its settings as a typed union.
    target = {"layerId": _LAYER_ID} if standalone else {}
    variants = []
    for tool_id, keys in (
        ("basic_pen", brush_settings.PEN_STROKE_KEYS),
        ("basic_eraser", brush_settings.ERASER_STROKE_KEYS),
    ):
        settings = {key: SETTING_PROPERTIES[key] for key in sorted(keys)}
        settings["size"] = {
            **settings["size"],
            "description": "Brush size in canvas pixels at full pressure.",
        }
        variants.append(
            schema(
                {
                    **target,
                    "toolId": {"type": "string", "const": tool_id},
                    "points": _POINTS,
                    "settings": schema(settings),
                },
                [*target, "toolId", "points", "settings"],
                edit=standalone,
            )
        )
    return {"type": "object", "oneOf": variants}


_PEN_EXAMPLE = {
    "toolId": "basic_pen",
    "points": [{"x": 8, "y": 12}, {"x": 24, "y": 12}],
    "settings": {"size": 3, "color": "#70A8D6"},
}
DRAW_STROKE_EXAMPLE = {"layerId": "YOUR_LAYER_ID", **_PEN_EXAMPLE}
DRAW_STROKES_EXAMPLE = {
    "layerId": "YOUR_LAYER_ID",
    "strokes": [
        _PEN_EXAMPLE,
        {
            "toolId": "basic_pen",
            "points": [{"x": 10, "y": 20}, {"x": 20, "y": 20}],
            "settings": {"size": 1.5, "color": "#F1D597"},
        },
    ],
}

DRAW_STROKE_SCHEMA = {**_stroke_variants(standalone=True), "examples": [DRAW_STROKE_EXAMPLE]}
DRAW_STROKES_SCHEMA = {
    **schema(
        {
            "layerId": _LAYER_ID,
            "strokes": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_STROKES,
                "items": _stroke_variants(standalone=False),
                "description": "Round pen or eraser strokes in array order on the same layer.",
            },
        },
        ["layerId", "strokes"],
        edit=True,
    ),
    "examples": [DRAW_STROKES_EXAMPLE],
}


def _example_text(example: dict[str, Any]) -> str:
    return (
        "\nExample arguments (replace YOUR_LAYER_ID with the target raster-layer ID)\n"
        + json.dumps(example, separators=(",", ":"))
    )


DRAW_STROKE_DESCRIPTION = (
    "Draw one round pen or eraser stroke on a raster layer as one Undo entry. "
    "Use canvas pixel coordinates, settings.size for brush thickness and settings.color "
    "for the pen color. Use loomstroke_draw_strokes to send several strokes together."
    + _example_text(DRAW_STROKE_EXAMPLE)
)
DRAW_STROKES_DESCRIPTION = (
    f"Draw up to {MAX_STROKES} round pen or eraser strokes in array order on one raster layer. "
    "Set points and brush settings separately for each stroke. The whole batch is one Undo entry. "
    f"Limits are {MAX_POINTS:,} points and {MAX_STROKE_REGION_PIXELS:,} region pixels per stroke, "
    f"with {MAX_BATCH_POINTS:,} points and {MAX_BATCH_REGION_PIXELS:,} region pixels per call."
    + _example_text(DRAW_STROKES_EXAMPLE)
)
