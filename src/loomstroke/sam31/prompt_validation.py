"""Validate the AI Select inputs that the SAM31 image bridge can consume."""

from __future__ import annotations

from loomstroke.ai_selection.data_model import (
    BlockedReason,
    Sam3Prompt,
    Sam3Readiness,
    Sam3Warning,
)
from loomstroke.ai_selection.worker_client import AiSelectionWorkerUnavailable


def bridge_capabilities() -> dict[str, bool]:
    """Adapter support, independent of whether the local runtime is set up."""
    return {
        "textPrompt": True,
        "pointPrompt": True,
        "boxPrompt": True,
        "boxExemplarPrompt": True,
        "maskPrompt": False,
        "assetExemplarPrompt": False,
        "negativeTextPrompt": False,
        "multipleBoxes": True,
        "combinedPointsAndBoxes": False,
        "pointRefine": True,
        "textRefine": False,
        "boxRefine": False,
        "cancelJob": False,
    }


def prompt_text(prompt: Sam3Prompt) -> str:
    """Use the caller's normalized text when supplied, retaining the source prompt."""
    return (prompt.normalized_text or "").strip() or (prompt.text or "").strip()


def _blocked(code: str, message: str, action: str) -> AiSelectionWorkerUnavailable:
    return AiSelectionWorkerUnavailable(
        message,
        readiness=Sam3Readiness(
            state="blocked",
            runtime="sam3_1",
            blocked_reasons=(BlockedReason(code=code, message=message, action=action),),
            recommended_actions=(action,),
            diagnostics={"capabilities": bridge_capabilities()},
        ),
    )


def validate_prompt(
    prompt: Sam3Prompt, *, refine: bool = False
) -> tuple[str, tuple[Sam3Warning, ...]]:
    """Return the active worker mode and warnings before starting the worker.

    Inactive geometry survives UI mode changes. Report it without changing
    the active mode; mixed mode makes both geometries active and must not
    silently discard either. Text accompanying point refine describes the
    existing candidate; refine adjusts that object using points.
    """
    mode = prompt.mode
    if mode not in {"text", "point", "click", "box", "exemplar", "mixed"}:
        raise _blocked(
            "sam31_prompt_mode_unsupported",
            "This prompt mode is not supported by the SAM 3.1 image backend.",
            "Use text, points, or one box instead.",
        )
    if prompt.negative_text and prompt.negative_text.strip():
        raise _blocked(
            "sam31_negative_text_unsupported",
            "SAM 3.1 does not support negative text prompts.",
            "Remove negativeText; use negative points with a point prompt instead.",
        )
    if mode in {"exemplar", "mixed"} and prompt.exemplar_asset_ids:
        raise _blocked(
            "sam31_asset_exemplar_unsupported",
            "SAM 3.1 exemplar selection requires a box, not exemplar assets.",
            "Remove exemplarAssetIds and provide one box in exemplar mode.",
        )
    if mode == "mixed" and prompt.mask_asset_id:
        raise _blocked(
            "sam31_mask_prompt_unsupported",
            "SAM 3.1 does not support mask prompts for segmentation.",
            "Remove maskAssetId and use text, points, or one box instead.",
        )
    if mode == "mixed" and prompt.points and prompt.boxes:
        raise _blocked(
            "sam31_combined_geometry_unsupported",
            "SAM 3.1 cannot combine points and boxes in one segmentation request.",
            "Use points or one box in separate requests.",
        )
    if mode == "mixed":
        mode = "point" if prompt.points else "box" if prompt.boxes else "text"
    if refine and (mode not in {"point", "click"} or not prompt.points):
        raise _blocked(
            "sam31_refine_requires_points",
            "SAM 3.1 refines an existing candidate using positive or negative points.",
            "Provide a point prompt for refine; run segment for new text or box prompts.",
        )
    if mode == "text" and not prompt_text(prompt):
        raise _blocked(
            "sam31_text_prompt_required",
            "Text selection requires prompt text.",
            "Provide non-empty text or choose a geometry prompt mode.",
        )
    if mode in {"point", "click"} and not prompt.points:
        raise _blocked(
            "sam31_point_prompt_required",
            "Point selection requires at least one point.",
            "Provide points in the source image coordinate system.",
        )
    if mode in {"box", "exemplar"}:
        if not prompt.boxes:
            raise _blocked(
                "sam31_box_prompt_required",
                "Box selection requires one box.",
                "Provide one box in the source image coordinate system.",
            )
        if len(prompt.boxes) > 32:
            raise _blocked(
                "sam31_box_limit_exceeded",
                "SAM 3.1 accepts at most 32 boxes per segmentation request.",
                "Submit at most 32 boxes per request.",
            )
    return mode, _inactive_warnings(prompt, mode)


def _inactive_warnings(prompt: Sam3Prompt, mode: str) -> tuple[Sam3Warning, ...]:
    inactive: list[str] = []
    if mode not in {"point", "click"} and prompt.points:
        inactive.append("points")
    if mode not in {"box", "exemplar"} and prompt.boxes:
        inactive.append("boxes")
    if prompt.mask_asset_id:
        inactive.append("maskAssetId")
    if prompt.exemplar_asset_ids:
        inactive.append("exemplarAssetIds")
    return (
        (
            Sam3Warning(
                code="sam31_inactive_prompt_inputs_ignored",
                message="Inputs inactive in the selected mode were ignored: " + ", ".join(inactive),
                action="Clear inactive inputs or choose the corresponding prompt mode.",
            ),
        )
        if inactive
        else ()
    )
