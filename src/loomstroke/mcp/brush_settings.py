"""Validate brush values and delegate live settings/presets to ToolManager."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .editing_common import check_gui_thread, finite_number, invalid
from .types import McpStructuredError, ReasonCode

BRUSH_IDS = ("basic_pen", "basic_eraser")
NUMBER_LIMITS = {
    "size": (0.1, 1000.0),
    "opacity": (0.0, 1.0),
    "hardness": (0.0, 1.0),
    "spacing": (0.01, 3.0),
    "flow": (0.0, 1.0),
    "tone_range": (0.0, 1.0),
    "tone_gamma": (0.1, 2.0),
    "stamp_density": (0.0, 1.0),
    "smoothing": (0.0, 1.0),
    "recontour_smoothness": (0.0, 100.0),
}
BOOLEAN_KEYS = {"anti_alias", "pressure_sensitive", "tilt_angle", "recontour"}
PEN_STROKE_KEYS = {
    "size",
    "opacity",
    "tone_range",
    "tone_gamma",
    "color",
    "pressure_sensitive",
    "tilt_angle",
}
ERASER_STROKE_KEYS = {"size", "pressure_sensitive"}
ERASER_KEYS = {"size", "opacity", "hardness", "spacing", "anti_alias"}


def brush_id(arguments: Mapping[str, Any]) -> str:
    tool_id = arguments.get("toolId")
    if tool_id not in BRUSH_IDS:
        raise invalid("'toolId' must be basic_pen or basic_eraser.", "toolId")
    return tool_id


def color_rgba(value: Any) -> tuple[int, ...]:
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
        return tuple(int(value[index : index + 2], 16) for index in range(1, len(value), 2))
    if (
        isinstance(value, (list, tuple))
        and len(value) in (3, 4)
        and all(type(channel) is int and 0 <= channel <= 255 for channel in value)
    ):
        return tuple(value)
    raise invalid("'color' must be #RRGGBB, #RRGGBBAA or an RGB/RGBA8 array.", "settings.color")


def validate_settings(value: Any, tool_id: str, *, stroke: bool = False) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise invalid("'settings' must be an object.", "settings")
    allowed = (
        (PEN_STROKE_KEYS if tool_id == "basic_pen" else ERASER_STROKE_KEYS)
        if stroke
        else (
            ERASER_KEYS
            if tool_id == "basic_eraser"
            else (NUMBER_LIMITS.keys() | BOOLEAN_KEYS | {"color"}) - {"anti_alias"}
        )
    )
    result = {}
    for key, item in value.items():
        if key not in allowed:
            raise invalid(f"Unsupported brush setting: {key}.", f"settings.{key}")
        if key in NUMBER_LIMITS:
            low, high = NUMBER_LIMITS[key]
            result[key] = finite_number(item, f"settings.{key}", minimum=low, maximum=high)
        elif key in BOOLEAN_KEYS:
            if not isinstance(item, bool):
                raise invalid(f"'{key}' must be a boolean.", f"settings.{key}")
            result[key] = item
        elif key == "color":
            rgba = color_rgba(item)
            result[key] = item if stroke else list(rgba)
    if "stamp_density" in result and "spacing" in result:
        raise invalid("Use either stamp_density or spacing, not both.", "settings")
    return result


def manager_for(service: Any, tool_id: str | None = None) -> Any:
    check_gui_thread()
    provider = getattr(service, "tool_manager_provider", None)
    manager = provider() if callable(provider) else None
    if manager is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "The live brush library is not connected.",
            details={"capability": "live_brush_settings"},
        )
    if tool_id is not None and not manager.has_tool(tool_id):
        raise McpStructuredError(
            ReasonCode.NOT_FOUND,
            "The requested brush is not registered.",
            details={"toolId": tool_id},
        )
    return manager


def list_brushes(service: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    selected = brush_id(arguments) if "toolId" in arguments else None
    manager = manager_for(service, selected)
    brushes = []
    for descriptor in manager.get_registered_tools():
        if descriptor.id not in BRUSH_IDS or (selected and selected != descriptor.id):
            continue
        brushes.append(
            {
                "toolId": descriptor.id,
                "name": descriptor.display_name,
                "settings": manager.get_tool_settings(descriptor.id),
                "presets": [
                    {"name": preset.name, "settings": dict(preset.values)}
                    for preset in manager.get_tool_presets(descriptor.id)
                ],
            }
        )
    return {"brushes": brushes, "library": "tool_presets", "customTipLibraryAvailable": False}


def settings_snapshot(service: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    tool_id = brush_id(arguments)
    manager = manager_for(service, tool_id)
    return {"toolId": tool_id, "settings": manager.get_tool_settings(tool_id)}


def update_settings(
    service: Any, arguments: Mapping[str, Any], *, preset: bool = False
) -> dict[str, Any]:
    tool_id = brush_id(arguments)
    manager = manager_for(service, tool_id)
    if preset:
        name = arguments.get("presetName")
        if not isinstance(name, str) or not name or len(name) > 256:
            raise invalid(
                "'presetName' must be a non-empty name of at most 256 characters.", "presetName"
            )
        match = next(
            (item for item in manager.get_tool_presets(tool_id) if item.name == name), None
        )
        if match is None:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "The requested brush preset was not found.",
                details={"presetName": name},
            )
        validate_settings(match.values, tool_id)
        manager.apply_preset(tool_id, name)
    else:
        values = validate_settings(arguments.get("settings"), tool_id)
        for key, value in values.items():
            manager.set_tool_setting(tool_id, key, value)
    return {
        "status": "completed",
        "toolId": tool_id,
        "settings": manager.get_tool_settings(tool_id),
    }
