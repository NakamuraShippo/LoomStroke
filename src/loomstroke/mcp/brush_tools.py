"""MCP brush schemas and dispatch, separate from rendering/settings handling."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import brush_settings, brush_stroke
from .brush_tool_schemas import (
    DRAW_STROKE_DESCRIPTION,
    DRAW_STROKE_SCHEMA,
    DRAW_STROKES_DESCRIPTION,
    DRAW_STROKES_SCHEMA,
    POINT,
    SETTING_PROPERTIES,
    SETTINGS,
    TOOL_ID,
    schema,
)
from .editing_common import edit_operation, invalid
from .types import McpToolSpec

_POINT = POINT
_SETTING_PROPERTIES = SETTING_PROPERTIES
_SETTINGS = SETTINGS
_schema = schema

TOOLS = (
    McpToolSpec("loomstroke_draw_stroke", DRAW_STROKE_DESCRIPTION, DRAW_STROKE_SCHEMA),
    McpToolSpec("loomstroke_draw_strokes", DRAW_STROKES_DESCRIPTION, DRAW_STROKES_SCHEMA),
    McpToolSpec(
        "loomstroke_list_brushes",
        "List live pen/eraser tools and their registered tool presets.",
        _schema({"toolId": TOOL_ID}),
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_brush_settings",
        "Read the live brush settings.",
        _schema({"toolId": TOOL_ID}, ["toolId"]),
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_set_brush_settings",
        "Update live brush settings through ToolManager.",
        _schema({"toolId": TOOL_ID, "settings": _SETTINGS}, ["toolId", "settings"], edit=True),
    ),
    McpToolSpec(
        "loomstroke_apply_brush_preset",
        "Apply a named registered brush preset through ToolManager.",
        _schema(
            {
                "toolId": TOOL_ID,
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
    if name == "loomstroke_draw_strokes":
        return await brush_stroke.draw_strokes(service, name, arguments)
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
