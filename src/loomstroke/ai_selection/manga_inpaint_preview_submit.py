"""Submit helpers for SAM3 manga inpaint preview requests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from .generation_preview_submit import (
    GenerationSubmitter,
    submit_ai_effect_preview_generation_request,
)
from .manga_inpaint_prepare import SAM3_MANGA_INPAINT_PREPARATION_SCHEMA_VERSION
from .metadata_scrub import scrub_sam3_metadata

SAM3_MANGA_INPAINT_PREVIEW_SUBMIT_RESULT_SCHEMA_VERSION = (
    "sam3.manga_inpaint_preview_submit_result.v1"
)


class Sam3MangaInpaintPreviewSubmitError(ValueError):
    """Raised when a manga inpaint preview request cannot be submitted."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


async def submit_manga_inpaint_preview_request(
    request: Mapping[str, object],
    submit_generation: GenerationSubmitter,
    *,
    action_id: str | None = None,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    effect_layer_id: str | None = None,
    slot_id: str | None = None,
    result_asset_collector: Callable[[Sequence[Mapping[str, object]]], None] | None = None,
) -> dict[str, object]:
    """Submit a manga inpaint preview request through the generic SAM3 adapter."""

    request_payload = request if isinstance(request, Mapping) else None
    manga_inpaint, blocked_reasons = _manga_inpaint_metadata(request_payload)
    if blocked_reasons:
        return _submit_result(
            status="blocked",
            handled=False,
            action_id=action_id,
            blocked_reason_codes=blocked_reasons,
        )
    assert request_payload is not None
    assert manga_inpaint is not None
    base_result = await submit_ai_effect_preview_generation_request(
        request_payload,
        submit_generation,
        action_id=action_id,
        candidate_set_id=candidate_set_id,
        candidate_id=candidate_id or _optional_text(manga_inpaint.get("candidateId")),
        effect_layer_id=effect_layer_id,
        slot_id=slot_id,
        result_asset_collector=result_asset_collector,
    )
    return _from_ai_effect_submit_result(base_result, manga_inpaint)


def _manga_inpaint_metadata(
    request: Mapping[str, object] | None,
) -> tuple[Mapping[str, object] | None, list[str]]:
    if request is None:
        return None, ["manga_inpaint_preview_request_missing"]
    metadata = _mapping(request.get("metadata"))
    manga_inpaint = _mapping(metadata.get("mangaInpaint") if metadata is not None else None)
    blocked_reason: str | None = None
    if manga_inpaint is None:
        blocked_reason = "manga_inpaint_metadata_missing"
    elif manga_inpaint.get("schemaVersion") != SAM3_MANGA_INPAINT_PREPARATION_SCHEMA_VERSION:
        blocked_reason = "manga_inpaint_metadata_schema_invalid"
    elif manga_inpaint.get("ready") is not True:
        blocked_reason = "manga_inpaint_preparation_not_ready"
    elif _optional_text(manga_inpaint.get("candidateId")) is None:
        blocked_reason = "manga_inpaint_candidate_id_missing"
    elif _optional_text(manga_inpaint.get("inpaintMaskAssetId")) is None:
        blocked_reason = "manga_inpaint_mask_asset_missing"
    if blocked_reason is not None:
        return None, [blocked_reason]
    assert manga_inpaint is not None
    return manga_inpaint, []


def _from_ai_effect_submit_result(
    base_result: Mapping[str, object],
    manga_inpaint: Mapping[str, object],
) -> dict[str, object]:
    status = _optional_text(base_result.get("status")) or "blocked"
    handled = bool(base_result.get("handled", False))
    preview_submit = _mapping(base_result.get("previewSubmit"))
    enriched_preview_submit: dict[str, object] | None = None
    if status == "submitted" and preview_submit is not None:
        enriched_preview_submit = dict(preview_submit)
        enriched_preview_submit["mangaInpaint"] = _manga_inpaint_summary(manga_inpaint)

    result = _submit_result(
        status=status,
        handled=handled,
        action_id=_optional_text(base_result.get("actionId")),
        preview_submit=enriched_preview_submit,
        blocked_reason_codes=_string_list(base_result.get("blockedReasonCodes")),
    )
    error = _mapping(base_result.get("error"))
    if error is not None:
        result["error"] = scrub_sam3_metadata(dict(error))
    return result


def _manga_inpaint_summary(manga_inpaint: Mapping[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {
        "schemaVersion": SAM3_MANGA_INPAINT_PREPARATION_SCHEMA_VERSION,
        "requiresUserConfirmation": bool(manga_inpaint.get("requiresUserConfirmation", True)),
    }
    for source_key, target_key in (
        ("candidateId", "candidateId"),
        ("category", "category"),
        ("inpaintMaskAssetId", "inpaintMaskAssetId"),
        ("protectedMaskAssetId", "protectedMaskAssetId"),
    ):
        text = _optional_text(manga_inpaint.get(source_key))
        if text is not None:
            summary[target_key] = text
    return dict(scrub_sam3_metadata(summary))


def _submit_result(
    *,
    status: str,
    handled: bool,
    action_id: str | None,
    preview_submit: Mapping[str, object] | None = None,
    blocked_reason_codes: Sequence[str] = (),
) -> dict[str, object]:
    return {
        "schemaVersion": SAM3_MANGA_INPAINT_PREVIEW_SUBMIT_RESULT_SCHEMA_VERSION,
        "status": status,
        "handled": bool(handled),
        "actionId": action_id,
        "previewSubmit": (
            scrub_sam3_metadata(dict(preview_submit)) if preview_submit is not None else None
        ),
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            result.append(text)
    return result


__all__ = [
    "SAM3_MANGA_INPAINT_PREVIEW_SUBMIT_RESULT_SCHEMA_VERSION",
    "Sam3MangaInpaintPreviewSubmitError",
    "submit_manga_inpaint_preview_request",
]
