"""Submit helpers for preview-only SAM3 AI Effect generation requests."""

from __future__ import annotations

import contextlib
import copy
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence

from .generation_preview_request import SAM3_AI_EFFECT_PREVIEW_REQUEST_SCHEMA_VERSION
from .metadata_scrub import scrub_sam3_metadata

SAM3_AI_EFFECT_PREVIEW_SUBMIT_RESULT_SCHEMA_VERSION = "sam3.ai_effect_preview_submit_result.v1"

_METADATA_SOURCE = "sam3.ai_effect.preview_generation"

GenerationSubmitter = Callable[
    [Mapping[str, object]],
    Mapping[str, object] | Awaitable[Mapping[str, object]],
]


class Sam3AIEffectPreviewSubmitError(ValueError):
    """Raised when a SAM3 AI Effect preview request cannot be submitted."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


async def submit_ai_effect_preview_generation_request(
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
    """Submit a validated preview-only request through an injected generation boundary.

    ``result_asset_collector`` receives the raw (pre-scrub) asset mappings
    from the submitter outcome so callers can run in-memory post-processing
    (e.g. diff QA) on generated image bytes. The returned payload stays
    scrubbed regardless; collector failures never affect the submit result.
    """

    if not callable(submit_generation):
        return _submit_result(
            status="blocked",
            handled=False,
            action_id=action_id,
            blocked_reason_codes=["generation_submitter_missing"],
        )
    request_payload = request if isinstance(request, Mapping) else None
    validation_errors = _preview_request_blocked_reason_codes(request_payload)
    if validation_errors:
        return _submit_result(
            status="blocked",
            handled=False,
            action_id=action_id,
            blocked_reason_codes=validation_errors,
        )
    assert request_payload is not None
    request_copy = copy.deepcopy(dict(request_payload))

    try:
        submitted = submit_generation(request_copy)
        if inspect.isawaitable(submitted):
            submitted = await submitted
        if not isinstance(submitted, Mapping):
            raise Sam3AIEffectPreviewSubmitError(
                "generation_submit_result_invalid",
                "SAM3 preview generation submitter must return a mapping.",
            )
    except Exception as exc:  # noqa: BLE001
        # Duck-typed privacy detection (no mcp import from this layer): the
        # host's fail-close wrapper raises with reason_code "privacy_denied"
        # when privacy/allow_external_services is off.
        if getattr(exc, "reason_code", None) == "privacy_denied":
            blocked_reason_codes = ["external_services_disabled"]
        else:
            blocked_reason_codes = ["generation_submit_failed"]
        return _submit_result(
            status="failed",
            handled=False,
            action_id=action_id,
            blocked_reason_codes=blocked_reason_codes,
            error={"class": exc.__class__.__name__},
        )

    if result_asset_collector is not None:
        # Collectors must never affect the submit result.
        with contextlib.suppress(Exception):
            result_asset_collector(_raw_asset_mappings(submitted.get("assets")))

    metadata = _mapping(request_payload.get("metadata"))
    sam3_preview = _mapping(metadata.get("sam3Preview") if metadata is not None else None)
    layer_id = _optional_text(request_payload.get("layerId"))
    preview_submit = {
        "submitted": True,
        "candidateSetId": candidate_set_id
        or _optional_text(sam3_preview.get("candidateSetId") if sam3_preview else None),
        "candidateId": candidate_id
        or _optional_text(sam3_preview.get("candidateId") if sam3_preview else None),
        "effectLayerId": effect_layer_id or layer_id,
        "slotId": slot_id,
        "requestSummary": _request_summary(request_payload),
        "job": _job_summary(submitted),
    }
    return _submit_result(
        status="submitted",
        handled=True,
        action_id=action_id,
        preview_submit=preview_submit,
    )


def _preview_request_blocked_reason_codes(
    request: Mapping[str, object] | None,
) -> list[str]:
    if request is None:
        return ["preview_request_missing"]
    reason_codes: list[str] = []
    if _optional_text(request.get("layerId")) is None:
        reason_codes.append("preview_request_layer_missing")
    if request.get("applyToCanvas") is not False:
        reason_codes.append("preview_request_not_asset_only")
    if request.get("persistOverrides") is not False:
        reason_codes.append("preview_request_persists_overrides")

    metadata = _mapping(request.get("metadata"))
    if metadata is None or metadata.get("source") != _METADATA_SOURCE:
        reason_codes.append("preview_request_source_invalid")
    if metadata is None or metadata.get("candidateRequest") is not True:
        reason_codes.append("preview_request_lineage_missing")
    sam3_preview = _mapping(metadata.get("sam3Preview") if metadata is not None else None)
    if (
        sam3_preview is None
        or sam3_preview.get("schemaVersion") != SAM3_AI_EFFECT_PREVIEW_REQUEST_SCHEMA_VERSION
    ):
        reason_codes.append("preview_request_lineage_missing")
    return list(dict.fromkeys(reason_codes))


def _request_summary(request: Mapping[str, object]) -> dict[str, object]:
    metadata = _mapping(request.get("metadata")) or {}
    summary: dict[str, object] = {
        "layerId": _optional_text(request.get("layerId")),
        "applyToCanvas": bool(request.get("applyToCanvas", True)),
        "persistOverrides": bool(request.get("persistOverrides", True)),
        "metadataSource": _optional_text(metadata.get("source")),
        "candidateRequest": bool(metadata.get("candidateRequest", False)),
    }
    candidate_count = _optional_int(metadata.get("candidateCount"))
    if candidate_count is not None:
        summary["candidateCount"] = candidate_count
    return summary


def _job_summary(submitted: Mapping[str, object]) -> dict[str, object]:
    safe = _mapping(scrub_sam3_metadata(dict(submitted))) or {}
    summary: dict[str, object] = {}
    for source_key, target_key in (
        ("jobId", "jobId"),
        ("id", "jobId"),
        ("status", "status"),
        ("jobClass", "jobClass"),
        ("relatedLayerId", "relatedLayerId"),
    ):
        value = _optional_text(safe.get(source_key))
        if value is not None and target_key not in summary:
            summary[target_key] = value
    progress = _optional_number(safe.get("progress"))
    if progress is not None:
        summary["progress"] = progress
    result_asset_ids = _string_list(safe.get("resultAssetIds"))
    if result_asset_ids:
        summary["resultAssetIds"] = result_asset_ids
    assets = _asset_summaries(safe.get("assets"))
    if assets:
        summary["assets"] = assets
    return summary


def _raw_asset_mappings(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _asset_summaries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    assets: list[dict[str, object]] = []
    for item in value:
        asset = _mapping(item)
        if asset is None:
            continue
        asset_summary: dict[str, object] = {}
        for source_key, target_key in (
            ("assetId", "assetId"),
            ("id", "assetId"),
            ("kind", "kind"),
            ("mimeType", "mimeType"),
        ):
            text = _optional_text(asset.get(source_key))
            if text is not None and target_key not in asset_summary:
                asset_summary[target_key] = text
        dimensions = _mapping(asset.get("dimensions"))
        if dimensions is not None:
            safe_dimensions = {
                key: value
                for key in ("width", "height")
                if (value := _optional_int(dimensions.get(key))) is not None
            }
            if safe_dimensions:
                asset_summary["dimensions"] = safe_dimensions
        if asset_summary:
            assets.append(asset_summary)
    return assets


def _submit_result(
    *,
    status: str,
    handled: bool,
    action_id: str | None,
    preview_submit: Mapping[str, object] | None = None,
    blocked_reason_codes: Sequence[str] = (),
    error: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": SAM3_AI_EFFECT_PREVIEW_SUBMIT_RESULT_SCHEMA_VERSION,
        "status": status,
        "handled": bool(handled),
        "actionId": action_id,
        "previewSubmit": (
            scrub_sam3_metadata(dict(preview_submit)) if preview_submit is not None else None
        ),
        "blockedReasonCodes": list(blocked_reason_codes),
    }
    if error is not None:
        payload["error"] = scrub_sam3_metadata(dict(error))
    return payload


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _optional_number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    result: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            result.append(text)
    return result


__all__ = [
    "SAM3_AI_EFFECT_PREVIEW_SUBMIT_RESULT_SCHEMA_VERSION",
    "GenerationSubmitter",
    "Sam3AIEffectPreviewSubmitError",
    "submit_ai_effect_preview_generation_request",
]
