"""Custom tip strokes through the existing StampBrushCommand renderer."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from itertools import pairwise
from typing import Any

from loomstroke.core.commands import BrushSettings, StampBrushCommand

from .brush_asset_tools import brush_tip
from .brush_settings import color_rgba, validate_settings
from .brush_stroke import MAX_STROKE_REGION_PIXELS, _points, _submit_stroke, _target
from .brush_tools import _POINT, _SETTING_PROPERTIES, _schema
from .editing_common import edit_operation, invalid
from .extension_services import require_service
from .types import McpStructuredError, McpToolSpec, ReasonCode

_KEYS = {
    "size",
    "opacity",
    "flow",
    "hardness",
    "spacing",
    "tone_range",
    "tone_gamma",
    "color",
    "pressure_sensitive",
}
TOOLS = (
    McpToolSpec(
        "loomstroke_draw_stamp_stroke",
        "Draw a Basic Pen custom-tip stroke using a stored brushId and explicit color/settings. "
        "Pressure, spacing, opacity, hardness and flow are applied. One undo entry; "
        "at most 2048 input points and 8192 generated stamps, region <=4,194,304 pixels.",
        _schema(
            {
                "layerId": {"type": "string", "minLength": 1},
                "brushId": {"type": "string", "minLength": 1},
                "points": {"type": "array", "minItems": 1, "maxItems": 2048, "items": _POINT},
                "settings": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {key: _SETTING_PROPERTIES[key] for key in _KEYS},
                },
                "hardnessMode": {"enum": ["radial", "tip"]},
            },
            ["layerId", "brushId", "points", "settings"],
            edit=True,
        ),
    ),
)


def _sample_points(points: list[Any], spacing: float, size: float) -> list[Any]:
    sampled = [points[0]]
    step = max(0.25, spacing * size * 2)
    for previous, point in pairwise(points):
        distance = math.hypot(point.x - previous.x, point.y - previous.y)
        count = max(1, math.ceil(distance / step))
        if len(sampled) + count > 8192:
            raise invalid("The stroke exceeds 8192 stamps; increase spacing or split it.")
        for index in range(1, count + 1):
            fraction = index / count
            sampled.append(
                replace(
                    point,
                    x=previous.x + (point.x - previous.x) * fraction,
                    y=previous.y + (point.y - previous.y) * fraction,
                    pressure=previous.pressure + (point.pressure - previous.pressure) * fraction,
                )
            )
    return sampled


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    store = require_service(service, "brush_store")
    _, path = brush_tip(store, arguments["brushId"])
    settings = validate_settings(arguments["settings"], "basic_pen")
    if set(settings) - _KEYS or "color" not in settings:
        raise invalid("Stamp settings require an explicit color and only documented fields.")
    async with edit_operation(service, name, arguments) as canvas:
        layer = _target(canvas, arguments["layerId"], "basic_pen")
        points = _points(arguments["points"], *canvas.get_canvas_size(), settings)
        values = {
            key: value
            for key, value in settings.items()
            if key not in {"color", "pressure_sensitive"}
        }
        rgba = color_rgba(settings["color"])
        values["color"] = rgba[:3]
        if len(rgba) == 4:
            values["opacity"] = values.get("opacity", 1.0) * rgba[3] / 255
        brush = BrushSettings(**values)
        xs, ys = [point.x for point in points], [point.y for point in points]
        if (max(xs) - min(xs) + 4 * brush.size + 4) * (
            max(ys) - min(ys) + 4 * brush.size + 4
        ) > MAX_STROKE_REGION_PIXELS:
            raise invalid("The custom-tip stroke region exceeds 4,194,304 pixels.")
        sampled = _sample_points(points, brush.spacing, brush.size)
        command = StampBrushCommand(
            sampled,
            brush,
            tip_path=str(path),
            hardness_mode=arguments.get("hardnessMode", "radial"),
            layer_id=layer.id,
        )
        previous_id, mask_edit = canvas.current_layer().id, getattr(
            canvas, "_mask_edit_flag", False
        )
        try:
            canvas.set_active_layer(layer.id)
            changed = _submit_stroke(canvas, layer, command, "MCP Custom Tip Stroke")
        except Exception as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "The custom-tip stroke failed.",
                details={"errorType": type(exc).__name__},
            ) from exc
        finally:
            canvas.set_active_layer(previous_id)
            canvas._mask_edit_flag = mask_edit
        return {
            "changed": changed,
            "layerId": layer.id,
            "brushId": arguments["brushId"],
            "stampCount": len(sampled),
            "undoable": changed,
        }
