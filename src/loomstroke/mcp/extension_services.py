"""Private dependency bridge for additive MCP features; no plugin API changes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .types import McpStructuredError, ReasonCode


def get_services(service: Any) -> Mapping[str, Any]:
    provider = getattr(service, "_editing_services_provider", None)
    if provider is None:
        return {}
    result = provider()
    if not isinstance(result, Mapping):
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED, "The editing service bridge is invalid."
        )
    return result


def require_service(service: Any, key: str) -> Any:
    services = get_services(service)
    value = services.get(key)
    if value is None:
        factory = services.get(f"{key}_factory")
        if callable(factory):
            value = factory()
    if value is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "The requested application service is unavailable.",
            details={"service": key},
        )
    return value
