"""Expose and validate the existing effect registry's parameter contracts."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from jsonschema import Draft202012Validator

from loomstroke.core.filters import FilterRegistry

from .editing_common import invalid
from .paint_extension_arguments import object_schema
from .serialization import to_jsonable


def parameter_schema(filter_type: str) -> dict[str, Any]:
    filter_class = FilterRegistry.get(filter_type)
    if filter_class is None:
        raise invalid("The requested filter type is not registered.", "effectType")
    properties: dict[str, Any] = {}
    for param in filter_class().param_schema():
        kind = param.param_type
        if kind in {"int", "float"}:
            schema = {"type": "integer" if kind == "int" else "number"}
            if param.min_val is not None:
                schema["minimum"] = param.min_val
            if param.max_val is not None:
                schema["maximum"] = param.max_val
        elif kind == "bool":
            schema = {"type": "boolean"}
        elif kind == "enum":
            schema = {"enum": param.choices or []}
        elif kind == "curve_points":
            schema = {
                "type": "array",
                "minItems": 2,
                "maxItems": 64,
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "integer", "minimum": 0, "maximum": 255},
                },
            }
        elif kind == "gradient_stops":
            schema = {
                "type": "array",
                "minItems": 2,
                "maxItems": 64,
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "prefixItems": [
                        {"type": "number", "minimum": 0, "maximum": 1},
                        {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0, "maximum": 255},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                    ],
                },
            }
        else:
            raise invalid(
                "The registered filter has an unsupported parameter schema.", "effectType"
            )
        schema["default"] = to_jsonable(param.default)
        properties[param.key] = schema
    return object_schema(properties)


def validate_params(filter_type: str, params: dict[str, Any]) -> dict[str, Any]:
    schema = parameter_schema(filter_type)
    if next(Draft202012Validator(schema).iter_errors(params), None) is not None:
        raise invalid("Filter parameters do not match the registered parameter schema.", "params")
    for key in ("points", "gradient_stops"):
        if key in params:
            positions = [entry[0] for entry in params[key]]
            if any(right <= left for left, right in pairwise(positions)):
                raise invalid(
                    "Filter control points/stops must have strictly increasing positions.", "params"
                )
    return dict(params)
