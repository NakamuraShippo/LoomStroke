"""Schemas and strict argument conversion for MCP fill operations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from itertools import pairwise
from typing import Any

from .editing_common import finite_number, invalid

PATTERN_PRESETS = (
    "Checkerboard",
    "Horizontal Stripes",
    "Vertical Stripes",
    "Diagonal Stripes",
    "Dots",
    "Crosshatch",
)
_POINT = {
    "type": "object",
    "required": ["x", "y"],
    "properties": {
        key: {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000} for key in ("x", "y")
    },
    "additionalProperties": False,
}
_INTEGER_POINT = {
    **_POINT,
    "properties": {
        key: {"type": "integer", "minimum": -1_000_000, "maximum": 1_000_000} for key in ("x", "y")
    },
}
_COLOR = {
    "anyOf": [
        {"type": "string", "pattern": r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$"},
        {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
        },
    ]
}
_COMMON = {
    "layerId": {"type": "string", "minLength": 1, "maxLength": 128},
    "expectedRevision": {"type": "string", "minLength": 1, "maxLength": 128},
}
_BUCKET = {
    "seed": _INTEGER_POINT,
    "tolerance": {"type": "integer", "minimum": 0, "maximum": 255},
    "gapClose": {"type": "integer", "minimum": 0, "maximum": 10},
    "connectivity": {"type": "integer", "enum": [8]},
    "referenceMode": {"type": "string", "enum": ["layer"]},
}
FILL_SCHEMA = {
    "type": "object",
    "description": "Canvas and reference layer are limited to 1,048,576 pixels each.",
    "required": ["layerId", "seed", "color"],
    "properties": {
        **_COMMON,
        **_BUCKET,
        "color": _COLOR,
        "target": {"type": "string", "enum": ["active", "new_layer"]},
        "layerName": {"type": "string", "minLength": 1, "maxLength": 128},
    },
    "additionalProperties": False,
}
GRADIENT_SCHEMA = {
    "type": "object",
    "description": "The target backing image is limited to 16,777,216 pixels.",
    "required": ["layerId", "start", "end", "stops"],
    "properties": {
        **_COMMON,
        "start": _POINT,
        "end": _POINT,
        "type": {"type": "string", "enum": ["linear", "radial"]},
        "dither": {"type": "boolean"},
        "stops": {
            "type": "array",
            "minItems": 2,
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["position", "color"],
                "properties": {
                    "position": {"type": "number", "minimum": 0, "maximum": 1},
                    "color": _COLOR,
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}
PATTERN_SCHEMA = {
    "type": "object",
    "description": "Canvas/reference: 1,048,576 pixels each; scaled pattern: 16,777,216 pixels.",
    "required": ["layerId", "seed"],
    "oneOf": [{"required": ["patternAssetId"]}, {"required": ["patternPreset"]}],
    "properties": {
        **_COMMON,
        **_BUCKET,
        "patternAssetId": {"type": "string", "minLength": 1, "maxLength": 512},
        "patternPreset": {"type": "string", "enum": list(PATTERN_PRESETS)},
        "scale": {"type": "number", "minimum": 0.1, "maximum": 10},
        "rotation": {"type": "number", "const": 0},
        "offset": _INTEGER_POINT,
    },
    "additionalProperties": False,
}
SCHEMAS = {
    "loomstroke_fill": FILL_SCHEMA,
    "loomstroke_draw_gradient": GRADIENT_SCHEMA,
    "loomstroke_fill_pattern": PATTERN_SCHEMA,
}


def parse_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    schema = SCHEMAS.get(name)
    if schema is None:
        raise invalid("Unknown fill tool.")
    if set(arguments) - schema["properties"].keys():
        raise invalid("Unsupported fill option.")
    for field in schema["required"]:
        if field not in arguments:
            raise invalid(f"'{field}' is required.", field)
    parsed: dict[str, Any] = {"layerId": text(arguments["layerId"], "layerId", 128)}
    if name == "loomstroke_draw_gradient":
        parsed.update(_gradient_arguments(arguments))
        return parsed
    parsed["seed"] = point(arguments["seed"], "seed", integer=True)
    parsed["tolerance"] = integer(arguments.get("tolerance", 8), "tolerance", 0, 255)
    parsed["gapClose"] = integer(arguments.get("gapClose", 0), "gapClose", 0, 10)
    parsed["connectivity"] = choice(
        integer(arguments.get("connectivity", 8), "connectivity", 8, 8), "connectivity", {8}
    )
    choice(arguments.get("referenceMode", "layer"), "referenceMode", {"layer"})
    if name == "loomstroke_fill":
        parsed["color"] = color(arguments["color"], "color")
        parsed["target"] = choice(
            arguments.get("target", "active"), "target", {"active", "new_layer"}
        )
        parsed["layerName"] = text(arguments.get("layerName", "Fill"), "layerName", 128)
        if "layerName" in arguments and parsed["target"] != "new_layer":
            raise invalid("'layerName' requires target=new_layer.", "layerName")
        return parsed
    if ("patternAssetId" in arguments) == ("patternPreset" in arguments):
        raise invalid("Provide exactly one patternAssetId or patternPreset.")
    if "patternAssetId" in arguments:
        parsed["patternAssetId"] = text(arguments["patternAssetId"], "patternAssetId", 512)
    else:
        parsed["patternPreset"] = choice(
            arguments["patternPreset"], "patternPreset", set(PATTERN_PRESETS)
        )
    parsed["scale"] = finite_number(arguments.get("scale", 1), "scale", minimum=0.1, maximum=10)
    rotation = finite_number(arguments.get("rotation", 0), "rotation")
    if rotation != 0:
        raise invalid("Pattern rotation is not supported.", "rotation")
    parsed["offset"] = point(arguments.get("offset", {"x": 0, "y": 0}), "offset", integer=True)
    return parsed


def _gradient_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    start, end = point(arguments["start"], "start"), point(arguments["end"], "end")
    if start == end:
        raise invalid("Gradient start and end must differ.", "end")
    dither = arguments.get("dither", True)
    if not isinstance(dither, bool):
        raise invalid("'dither' must be a boolean.", "dither")
    return {
        "start": start,
        "end": end,
        "type": choice(arguments.get("type", "linear"), "type", {"linear", "radial"}),
        "dither": dither,
        "stops": gradient_stops(arguments["stops"]),
    }


def choice(value: Any, field: str, values: set[Any]) -> Any:
    if isinstance(value, bool) or not isinstance(value, str | int) or value not in values:
        raise invalid(f"Unsupported '{field}'.", field)
    return value


def text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise invalid(
            f"'{field}' must be a non-empty string of at most {maximum} characters.", field
        )
    return value


def integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    number = finite_number(value, field, minimum=minimum, maximum=maximum)
    if not number.is_integer():
        raise invalid(f"'{field}' must be an integer.", field)
    return int(number)


def point(value: Any, field: str, *, integer: bool = False) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y"}:
        raise invalid(f"'{field}' must have exactly x and y coordinates.", field)
    coordinates = tuple(
        finite_number(value[key], f"{field}.{key}", minimum=-1_000_000, maximum=1_000_000)
        for key in ("x", "y")
    )
    if integer and any(not number.is_integer() for number in coordinates):
        raise invalid(f"'{field}' requires integer coordinates.", field)
    return coordinates[0], coordinates[1]


def color(value: Any, field: str) -> tuple[int, int, int, int]:
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", value):
        try:
            channels = [int(value[index : index + 2], 16) for index in range(1, len(value), 2)]
        except ValueError as exc:
            raise invalid(f"'{field}' must be #RRGGBB or #RRGGBBAA.", field) from exc
        if len(channels) == 3:
            channels.append(255)
        return channels[0], channels[1], channels[2], channels[3]
    if isinstance(value, list | tuple) and len(value) == 4:
        channels = [
            integer(channel, f"{field}[{index}]", 0, 255) for index, channel in enumerate(value)
        ]
        return channels[0], channels[1], channels[2], channels[3]
    raise invalid(f"'{field}' must be hex or four RGBA8 channels.", field)


def gradient_stops(value: Any) -> list[tuple[float, tuple[int, int, int, int]]]:
    if not isinstance(value, list | tuple) or not 2 <= len(value) <= 5:
        raise invalid("'stops' must contain 2 to 5 ordered color stops.", "stops")
    stops = []
    for index, stop in enumerate(value):
        path = f"stops[{index}]"
        if not isinstance(stop, Mapping) or set(stop) != {"position", "color"}:
            raise invalid(f"'{path}' must contain position and color.", "stops")
        position = finite_number(stop["position"], f"{path}.position", minimum=0, maximum=1)
        stops.append((position, color(stop["color"], f"{path}.color")))
    if stops[0][0] != 0 or stops[-1][0] != 1 or any(a[0] >= b[0] for a, b in pairwise(stops)):
        raise invalid("'stops' must increase strictly from position 0 to position 1.", "stops")
    return stops
