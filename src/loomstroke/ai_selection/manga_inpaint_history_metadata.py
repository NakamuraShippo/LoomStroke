"""History metadata helpers for SAM3 manga inpaint preview submissions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .manga_inpaint_prepare import SAM3_MANGA_INPAINT_PREPARATION_SCHEMA_VERSION
from .metadata_scrub import scrub_sam3_metadata

SAM3_MANGA_INPAINT_PREVIEW_HISTORY_METADATA_SCHEMA_VERSION = (
    "sam3.manga_inpaint_preview_history_metadata.v1"
)

_METADATA_SOURCE = "sam3.manga_inpaint.preview_generation_submit"
_OPERATION = "manga_inpaint_preview_submit"


class Sam3MangaInpaintPreviewHistoryMetadataError(ValueError):
    """Raised when manga inpaint preview history metadata cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_manga_inpaint_preview_history_metadata(
    submit_result: Mapping[str, object],
) -> dict[str, object]:
    """Build a secret-free history metadata snapshot from a T4 manga submit result."""

    if not isinstance(submit_result, Mapping) or submit_result.get("status") != "submitted":
        raise Sam3MangaInpaintPreviewHistoryMetadataError(
            "manga_inpaint_preview_submit_not_submitted",
            "SAM3 manga inpaint preview history metadata requires a submitted result.",
        )
    preview_submit = _mapping(submit_result.get("previewSubmit"))
    if preview_submit is None:
        raise Sam3MangaInpaintPreviewHistoryMetadataError(
            "manga_inpaint_preview_submit_missing",
            "SAM3 manga inpaint preview history metadata requires previewSubmit.",
        )
    manga_inpaint = _validated_manga_inpaint_metadata(preview_submit)

    payload: dict[str, object] = {
        "schemaVersion": SAM3_MANGA_INPAINT_PREVIEW_HISTORY_METADATA_SCHEMA_VERSION,
        "source": _METADATA_SOURCE,
        "operation": _OPERATION,
        "mangaInpaint": _manga_inpaint_summary(manga_inpaint),
    }
    for source_key, target_key in (
        ("candidateSetId", "candidateSetId"),
        ("candidateId", "candidateId"),
        ("effectLayerId", "effectLayerId"),
        ("slotId", "slotId"),
    ):
        text = _optional_text(preview_submit.get(source_key))
        if text is not None:
            payload[target_key] = text
    request_summary = _request_summary(_mapping(preview_submit.get("requestSummary")) or {})
    if request_summary:
        payload["requestSummary"] = request_summary
    job = _job_summary(_mapping(preview_submit.get("job")) or {})
    if job:
        payload["job"] = job
    return dict(scrub_sam3_metadata(payload))


def _validated_manga_inpaint_metadata(
    preview_submit: Mapping[str, object],
) -> Mapping[str, object]:
    manga_inpaint = _mapping(preview_submit.get("mangaInpaint"))
    if manga_inpaint is None:
        raise Sam3MangaInpaintPreviewHistoryMetadataError(
            "manga_inpaint_history_manga_metadata_missing",
            "SAM3 manga inpaint preview history metadata requires mangaInpaint.",
        )
    if manga_inpaint.get("schemaVersion") != SAM3_MANGA_INPAINT_PREPARATION_SCHEMA_VERSION:
        raise Sam3MangaInpaintPreviewHistoryMetadataError(
            "manga_inpaint_history_manga_metadata_schema_invalid",
            "SAM3 manga inpaint preview history metadata has invalid manga schema.",
        )
    if _optional_text(manga_inpaint.get("candidateId")) is None:
        raise Sam3MangaInpaintPreviewHistoryMetadataError(
            "manga_inpaint_candidate_id_missing",
            "SAM3 manga inpaint preview history metadata requires a candidate id.",
        )
    if _optional_text(manga_inpaint.get("inpaintMaskAssetId")) is None:
        raise Sam3MangaInpaintPreviewHistoryMetadataError(
            "manga_inpaint_mask_asset_missing",
            "SAM3 manga inpaint preview history metadata requires an inpaint mask asset id.",
        )
    return manga_inpaint


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
    return summary


def _request_summary(summary: Mapping[str, object]) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for key in ("layerId", "metadataSource"):
        text = _optional_text(summary.get(key))
        if text is not None:
            snapshot[key] = text
    for key in ("applyToCanvas", "persistOverrides", "candidateRequest"):
        if key in summary:
            snapshot[key] = bool(summary.get(key))
    candidate_count = _optional_int(summary.get("candidateCount"))
    if candidate_count is not None:
        snapshot["candidateCount"] = candidate_count
    return snapshot


def _job_summary(job: Mapping[str, object]) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for key in ("jobId", "status", "jobClass", "relatedLayerId"):
        text = _optional_text(job.get(key))
        if text is not None:
            snapshot[key] = text
    progress = _optional_number(job.get("progress"))
    if progress is not None:
        snapshot["progress"] = progress
    result_asset_ids = _string_list(job.get("resultAssetIds"))
    if result_asset_ids:
        snapshot["resultAssetIds"] = result_asset_ids
    assets = _asset_summaries(job.get("assets"))
    if assets:
        snapshot["assets"] = assets
    return snapshot


def _asset_summaries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    assets: list[dict[str, object]] = []
    for item in value:
        asset = _mapping(item)
        if asset is None:
            continue
        asset_summary: dict[str, object] = {}
        for key in ("assetId", "kind", "mimeType"):
            text = _optional_text(asset.get(key))
            if text is not None:
                asset_summary[key] = text
        dimensions = _dimensions(asset.get("dimensions"))
        if dimensions:
            asset_summary["dimensions"] = dimensions
        if asset_summary:
            assets.append(asset_summary)
    return assets


def _dimensions(value: object) -> dict[str, int]:
    dimensions = _mapping(value)
    if dimensions is None:
        return {}
    snapshot: dict[str, int] = {}
    for key in ("width", "height"):
        number = _optional_int(dimensions.get(key))
        if number is not None:
            snapshot[key] = number
    return snapshot


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
    if isinstance(value, (int, float)):
        return value
    return None


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
    "SAM3_MANGA_INPAINT_PREVIEW_HISTORY_METADATA_SCHEMA_VERSION",
    "Sam3MangaInpaintPreviewHistoryMetadataError",
    "build_manga_inpaint_preview_history_metadata",
]
