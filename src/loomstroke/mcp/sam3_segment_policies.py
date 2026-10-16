"""MCP-only segment policy contract; GUI provider requests remain unchanged."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .editing_common import invalid
from .types import McpStructuredError, ReasonCode


def segment_policy_capabilities() -> dict[str, Any]:
    """These are entry-point limits, independent of worker readiness or credentials."""
    return {
        "maskResolutionPolicies": ["source"],
        "postprocessPolicy": False,
        "postprocessTool": "loomstroke_ai_select_refine_mask_asset",
    }


def validate_segment_policies(arguments: Mapping[str, Any]) -> None:
    resolution = arguments.get("maskResolutionPolicy")
    if resolution is not None and not isinstance(resolution, str):
        raise invalid("'maskResolutionPolicy' must be a string.", "maskResolutionPolicy")
    if (resolution or "").strip() not in {"", "source"}:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "MCP segmentation supports only the source mask resolution policy.",
            details={
                "argument": "maskResolutionPolicy",
                "blockedReasonCode": "sam3_mask_resolution_policy_unsupported",
                "supportedPolicies": ["source"],
            },
        )
    postprocess = arguments.get("postprocessPolicy")
    if postprocess is not None and not isinstance(postprocess, Mapping):
        raise invalid("'postprocessPolicy' must be an object.", "postprocessPolicy")
    if postprocess:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "MCP segmentation does not apply postprocessPolicy; refine the saved mask explicitly.",
            details={
                "argument": "postprocessPolicy",
                "blockedReasonCode": "sam3_segment_postprocess_policy_unsupported",
                "action": "Use loomstroke_ai_select_refine_mask_asset with supported options.",
            },
        )
