"""Strict JSON argument validation shared by paint extension tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator

from .editing_common import invalid
from .types import McpToolSpec

ID = {"type": "string", "minLength": 1, "maxLength": 256}
NAME = {"type": "string", "minLength": 1, "maxLength": 256, "pattern": r"\S"}
REVISION = {"expectedRevision": ID}


def object_schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def action_schema(actions: Mapping[str, tuple[dict[str, Any], tuple[str, ...]]]) -> dict[str, Any]:
    return {
        "oneOf": [
            object_schema({"action": {"const": action}, **REVISION, **props}, ("action", *required))
            for action, (props, required) in actions.items()
        ]
    }


def validate(
    tools: tuple[McpToolSpec, ...], name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    spec = next((tool for tool in tools if tool.name == name), None)
    if spec is None:
        raise invalid("Unknown paint extension tool.", "name")
    if not isinstance(arguments, Mapping):
        raise invalid("Arguments must be a JSON object.")
    result = dict(arguments)
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise invalid("Arguments must contain only finite JSON values.") from exc
    error = next(Draft202012Validator(spec.input_schema).iter_errors(result), None)
    if error is not None:
        field = str(next(iter(error.absolute_path), "arguments"))
        raise invalid("Arguments do not match the tool's input schema.", field)
    return result
