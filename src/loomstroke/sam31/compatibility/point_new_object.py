"""Shim: new-object point prompts are discarded by upstream output assembly.

Upstream bug (facebookresearch/sam3, present at least through commit
96914d2425f90a64f45ca977c2b5165418099543 / v0.1.0, see upstream issue #555):
``Sam3MultiplexTracking._build_sam2_output`` early-returns ``{}`` when the
prompted frame has no cached outputs yet, discarding the freshly generated
``refined_obj_id_to_mask`` before the merge loop runs. On a fresh session
this silently drops every mask created by a point-only prompt, even though
``add_sam2_new_points`` fully supports new objects and produces valid mask
logits (verified on real hardware, 2026-08-09).

The shim rebinds an output-assembly function with identical semantics minus
the early return. No weights, checkpoints, or architecture are touched.
"""

from __future__ import annotations

import inspect
from typing import Any

from .registry import CompatibilityStatus, ShimResult

SHIM_NAME = "point_new_object"
_MARKER = "_loomstroke_sam31_point_new_object_shim"

# The exact known-broken early return (fail-closed source verification).
_BROKEN_SNIPPET = 'if not frame_idx in inference_state["cached_frame_outputs"]:'
# The merge loop our replacement must mirror; if this is gone the upstream
# implementation changed in ways we do not understand.
_MERGE_SNIPPETS = (
    "refined_obj_id_to_mask",
    "obj_id_to_mask[obj_id] = refined_mask",
)


def _fixed_build_sam2_output(
    inference_state: Any,
    frame_idx: int,
    refined_obj_id_to_mask: Any = None,
) -> dict[Any, Any]:
    """Upstream semantics with the early return removed: merge over the
    cached outputs for the frame, falling back to an empty cache."""

    cached = inference_state["cached_frame_outputs"].get(frame_idx, {})
    obj_id_to_mask = dict(cached)
    if refined_obj_id_to_mask is not None:
        for obj_id, refined_mask in refined_obj_id_to_mask.items():
            if refined_mask is None:
                raise ValueError(f"Refined mask data must be provided for obj_id {obj_id}")
            obj_id_to_mask[obj_id] = refined_mask
    return obj_id_to_mask


def apply(predictor: Any) -> ShimResult:
    try:
        model = getattr(predictor, "model", None)
        if model is None or not hasattr(model, "_build_sam2_output"):
            return ShimResult(
                SHIM_NAME,
                CompatibilityStatus.UNSUPPORTED_VERSION,
                "predictor.model has no _build_sam2_output",
            )
        existing = getattr(model, _MARKER, None)
        if existing is not None:
            return ShimResult(SHIM_NAME, existing, "already evaluated")

        source = inspect.getsource(type(model)._build_sam2_output)
        has_merge = all(snippet in source for snippet in _MERGE_SNIPPETS)
        has_bug = _BROKEN_SNIPPET in source

        if not has_merge:
            status = CompatibilityStatus.UNSUPPORTED_VERSION
            detail = "output-assembly source no longer matches known upstream shape"
        elif not has_bug:
            status = CompatibilityStatus.NATIVE
            detail = "upstream early-return already fixed"
        else:
            model._build_sam2_output = _fixed_build_sam2_output
            status = CompatibilityStatus.SHIM_APPLIED
            detail = "instance-bound output assembly without the early return"

        setattr(model, _MARKER, status)
        return ShimResult(SHIM_NAME, status, detail)
    except Exception as exc:  # noqa: BLE001 - fail closed with diagnostics
        return ShimResult(SHIM_NAME, CompatibilityStatus.FAILED, f"{type(exc).__name__}: {exc}")


__all__ = ["SHIM_NAME", "apply"]
