"""Optional local recipe-generation validation before confirmation and queuing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .types import McpStructuredError


def preflight_generation(service: Any, normalized: Mapping[str, Any]) -> dict[str, Any]:
    validator = getattr(
        getattr(service, "_generation_submitter", None), "validate_production_request", None
    )
    if not callable(validator):
        return {"valid": True, "observed": False}
    try:
        validator(normalized)
    except McpStructuredError as exc:
        return {
            "valid": False,
            "observed": True,
            "reasonCode": exc.reason_code,
            "message": exc.message,
        }
    return {"valid": True, "observed": True}
