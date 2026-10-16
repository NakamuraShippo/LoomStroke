"""Validate exposed preset parameters before any production job is queued."""

from __future__ import annotations

from typing import Any

from .editing_common import finite_number, invalid


def validate_parameter_values(parameters: dict[str, dict], values: dict[str, Any]) -> None:
    for key, specification in parameters.items():
        value = values.get(key)
        if value is None:
            if specification.get("required_text"):
                raise invalid("A required production parameter is empty.", key)
            continue
        kind = specification.get("type")
        if kind in {"int", "float"}:
            bounds = specification.get("range") or {}
            numeric = finite_number(
                value, key, minimum=bounds.get("min"), maximum=bounds.get("max")
            )
            if kind == "int" and not numeric.is_integer():
                raise invalid("A production integer parameter has a fractional value.", key)
        elif kind == "bool" and not isinstance(value, bool):
            raise invalid("A production boolean parameter requires a boolean.", key)
        elif kind == "string":
            if not isinstance(value, str) or (
                specification.get("required_text") and not value.strip()
            ):
                raise invalid("A production text parameter requires text.", key)
        elif kind == "enum" and value not in specification.get("choices", []):
            raise invalid("A production parameter is outside its allowed choices.", key)
