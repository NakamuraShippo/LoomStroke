"""Cancel-aware package export orchestration for SAM3 video track runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .assets import Sam3AssetStore
from .metadata_scrub import scrub_sam3_metadata
from .video_track_cancel_store import Sam3VideoTrackCancelStoreResult
from .video_track_run import Sam3VideoTrackRunResult
from .video_track_run_exporter import (
    Sam3VideoTrackRunPackageExporterError,
    export_video_track_run_package,
)

SAM3_VIDEO_TRACK_CANCEL_PACKAGE_EXPORTER_RESULT_SCHEMA_VERSION = (
    "sam3.video_track_cancel_package_exporter_result.v1"
)

_REDACTED = "<redacted>"


class Sam3VideoTrackCancelPackageExporterError(ValueError):
    """Raised when a cancelled video track run package cannot be exported."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def export_cancelled_video_track_run_package(
    run_result: Sam3VideoTrackRunResult,
    cancel_store_result: Sam3VideoTrackCancelStoreResult,
    asset_store: Sam3AssetStore,
    output_dir: str | Path,
    *,
    allowed_roots: Sequence[str | Path],
    package_id: str | None = None,
    metadata_path: str = "video_track_run_package.json",
    include_previews: bool = True,
) -> dict[str, object]:
    """Export an acknowledged cancelled run result as an incomplete run package."""

    result = _run_result(run_result)
    cancel_result = _cancel_store_result(cancel_store_result)
    if not cancel_result.cancelled:
        raise Sam3VideoTrackCancelPackageExporterError(
            "video_track_cancel_package_not_cancelled",
            "SAM3 video track cancel package export requires an acknowledged cancel result.",
        )
    if result.status != "cancelled":
        raise Sam3VideoTrackCancelPackageExporterError(
            "video_track_cancel_package_run_status_invalid",
            "SAM3 video track cancel package export requires a cancelled run result.",
        )
    if result.request.request_id != cancel_result.cancel_result.request_id:
        raise Sam3VideoTrackCancelPackageExporterError(
            "video_track_cancel_package_request_mismatch",
            "SAM3 video track cancel package export requires matching request ids.",
        )

    try:
        export_result = export_video_track_run_package(
            result,
            asset_store,
            output_dir,
            allowed_roots=allowed_roots,
            package_id=package_id,
            metadata_path=metadata_path,
            include_previews=include_previews,
        )
    except Sam3VideoTrackRunPackageExporterError as exc:
        raise Sam3VideoTrackCancelPackageExporterError(exc.code, str(exc)) from exc

    export_payload = _scrubbed_mapping(export_result)
    payload: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_CANCEL_PACKAGE_EXPORTER_RESULT_SCHEMA_VERSION,
        "status": "exported",
        "requestId": result.request.request_id,
        "jobId": cancel_result.job_id,
        "runStatus": result.status,
        "cancelled": cancel_result.cancelled,
        "stored": cancel_result.stored,
        "completed": result.completed,
        "incomplete": not result.completed,
        "partial": result.partial,
        "export": export_payload,
        "cancelStore": _cancel_store_summary(cancel_result),
        "blockedReasonCodes": _string_list(export_payload.get("blockedReasonCodes")),
        "incompleteReasonCodes": _string_list(export_payload.get("incompleteReasonCodes")),
    }
    return _scrubbed_mapping(payload)


def _run_result(value: object) -> Sam3VideoTrackRunResult:
    if not isinstance(value, Sam3VideoTrackRunResult):
        raise Sam3VideoTrackCancelPackageExporterError(
            "video_track_cancel_package_run_result_invalid",
            "SAM3 video track cancel package export requires a video track run result.",
        )
    return value


def _cancel_store_result(value: object) -> Sam3VideoTrackCancelStoreResult:
    if not isinstance(value, Sam3VideoTrackCancelStoreResult):
        raise Sam3VideoTrackCancelPackageExporterError(
            "video_track_cancel_package_cancel_result_invalid",
            "SAM3 video track cancel package export requires a cancel store result.",
        )
    return value


def _cancel_store_summary(
    cancel_store_result: Sam3VideoTrackCancelStoreResult,
) -> dict[str, object]:
    job_status = cancel_store_result.job_status
    payload: dict[str, object] = {
        "schemaVersion": cancel_store_result.schema_version,
        "requestId": cancel_store_result.cancel_result.request_id,
        "jobId": cancel_store_result.job_id,
        "cancelStatus": cancel_store_result.cancel_result.status,
        "cancelled": cancel_store_result.cancelled,
        "stored": cancel_store_result.stored,
        "jobStatus": job_status.status if job_status is not None else None,
    }
    store_result = cancel_store_result.store_result
    if isinstance(store_result, Mapping):
        payload["storeStage"] = store_result.get("stage")
    return _scrubbed_mapping(payload)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _scrubbed_mapping(value: Mapping[str, object]) -> dict[str, Any]:
    scrubbed = scrub_sam3_metadata(value)
    redacted = _redact_source_locations(scrubbed)
    return dict(redacted) if isinstance(redacted, Mapping) else {}


def _redact_source_locations(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _redact_source_locations(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_source_locations(item) for item in value]
    if isinstance(value, str) and _looks_like_source_location(value):
        return _REDACTED
    return value


def _looks_like_source_location(value: str) -> bool:
    text = value.strip()
    lowered = text.lower()
    return (
        "://" in text
        or lowered.startswith(("http:", "https:", "file:"))
        or text.startswith(("/", "\\", "//", "\\\\"))
        or (len(text) >= 2 and text[1] == ":" and text[0].isalpha())
    )


__all__ = [
    "SAM3_VIDEO_TRACK_CANCEL_PACKAGE_EXPORTER_RESULT_SCHEMA_VERSION",
    "Sam3VideoTrackCancelPackageExporterError",
    "export_cancelled_video_track_run_package",
]
