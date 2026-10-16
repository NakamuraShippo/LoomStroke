"""Observe generation callback wiring without invoking a gateway or credentials."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

_Submitter = TypeVar("_Submitter", bound=Callable[..., Any])
_PRODUCTION_SUPPORTED_MARKER = "_loomstroke_production_generation_supported"


def layer_generation_submitter(callback: _Submitter) -> _Submitter:
    """Mark a callback as requiring an existing AI Effect layer."""
    setattr(callback, _PRODUCTION_SUPPORTED_MARKER, False)
    return callback


def generation_submitter_capabilities(service: Any) -> dict[str, bool]:
    """Read current wiring; unmarked injected callbacks retain their contract."""
    submitter = getattr(service, "_generation_submitter", None)
    available = callable(submitter)
    capability_observer = getattr(submitter, "capabilities", None)
    if available and callable(capability_observer):
        observed = capability_observer()
        return {
            "available": observed.get("available") is True,
            "productionCandidates": observed.get("productionCandidates") is True,
            "layerOnly": observed.get("layerOnly") is True,
        }
    layer_only = getattr(submitter, _PRODUCTION_SUPPORTED_MARKER, None) is False
    if available and layer_only:
        owner = getattr(submitter, "__self__", None)
        observer = getattr(owner, "_mcp_generation_capabilities", None)
        if callable(observer):
            available = observer().get("available") is True
    return {
        "available": available,
        "productionCandidates": available and not layer_only,
        "layerOnly": layer_only,
    }


def production_generation_blocks(service: Any) -> list[str]:
    """Return plan blockers for the recipe runner's layer-free payload."""
    capabilities = generation_submitter_capabilities(service)
    missing: list[str] = []
    if not capabilities["available"]:
        missing.append("generation_submitter")
    if capabilities["layerOnly"]:
        missing.append("production_recipe_generation_unsupported")
    return missing
