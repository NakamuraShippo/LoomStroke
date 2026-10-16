"""MCP brush schemas and dispatch, separate from rendering/settings handling."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import brush_settings, brush_stroke
from .editing_common import edit_operation, invalid
from .types import McpToolSpec

_TOOL_ID = {"type": "string", "enum": list(brush_settings.BRUSH_IDS)}
_SETTING_PROPERTIES = {
    key: {"type": "number", "minimum": bounds[0], "maximum": bounds[1]}
    for key, bounds in brush_settings.NUMBER_LIMITS.items()
}
_SETTING_PROPERTIES.update({key: {"type": "boolean"} for key in brush_settings.BOOLEAN_KEYS})
_SETTING_PROPERTIES["color"] = {
    "oneOf": [
        {"type": "string", "pattern": "^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$"},
        {
            "type": "array",
            "minItems": 3,
            "maxItems": 4,
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
        },
    ]
}
_SETTINGS = {"type": "object", "additionalProperties": False, "properties": _SETTING_PROPERTIES}
_POINT = {
    "type": "object",
    "required": ["x", "y"],
    "additionalProperties": False,
    "properties": {
        "x": {"type": "number", "minimum": 0},
        "y": {"type": "number", "minimum": 0},
        "pressure": {"type": "number", "minimum": 0, "maximum": 1},
        "tilt_x": {"type": "number", "minimum": -60, "maximum": 60},
        "tilt_y": {"type": "number", "minimum": -60, "maximum": 60},
        "time": {"type": "number", "minimum": 0, "maximum": 1e15},
    },
}


def _schema(
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


_DRAW_SCHEMA = _schema(
    {
        "toolId": _TOOL_ID,
        "layerId": {"type": "string", "minLength": 1, "maxLength": 256},
        "points": {
            "type": "array",
            "minItems": 1,
            "maxItems": brush_stroke.MAX_POINTS,
            "items": _POINT,
        },
        "settings": _SETTINGS,
    },
    ["toolId", "layerId", "points", "settings"],
    edit=True,
)
_DRAW_SCHEMA["allOf"] = [
    {
        "if": {"properties": {"toolId": {"const": "basic_pen"}}},
        "then": {
            "properties": {
                "settings": _schema(
                    {key: _SETTING_PROPERTIES[key] for key in brush_settings.PEN_STROKE_KEYS}
                )
            }
        },
        "else": {
            "properties": {
                "settings": _schema(
                    {key: _SETTING_PROPERTIES[key] for key in brush_settings.ERASER_STROKE_KEYS}
                )
            }
        },
    }
]

TOOLS = (
    McpToolSpec(
        "loomstroke_draw_stroke",
        "Draw a round pen or eraser stroke on a raster layer with explicit settings, as one Undo.",
        _DRAW_SCHEMA,
    ),
    McpToolSpec(
        "loomstroke_list_brushes",
        "List live pen/eraser tools and their registered tool presets.",
        _schema({"toolId": _TOOL_ID}),
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_brush_settings",
        "Read the live brush settings.",
        _schema({"toolId": _TOOL_ID}, ["toolId"]),
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_set_brush_settings",
        "Update live brush settings through ToolManager.",
        _schema({"toolId": _TOOL_ID, "settings": _SETTINGS}, ["toolId", "settings"], edit=True),
    ),
    McpToolSpec(
        "loomstroke_apply_brush_preset",
        "Apply a named registered brush preset through ToolManager.",
        _schema(
            {
                "toolId": _TOOL_ID,
                "presetName": {"type": "string", "minLength": 1, "maxLength": 256},
            },
            ["toolId", "presetName"],
            edit=True,
        ),
    ),
)


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if name == "loomstroke_draw_stroke":
        return await brush_stroke.draw_stroke(service, name, arguments)
    if name == "loomstroke_list_brushes":
        return brush_settings.list_brushes(service, arguments)
    if name == "loomstroke_get_brush_settings":
        return brush_settings.settings_snapshot(service, arguments)
    if name in {"loomstroke_set_brush_settings", "loomstroke_apply_brush_preset"}:
        async with edit_operation(service, name, arguments, require_canvas=False):
            return brush_settings.update_settings(
                service, arguments, preset=name == "loomstroke_apply_brush_preset"
            )
    raise invalid("Unknown brush tool.", "name")
