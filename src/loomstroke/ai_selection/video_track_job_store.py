"""Store helpers for SAM3 video track job status projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

from .assets import Sam3AssetStore
from .data_model import Sam3JobStatus
from .metadata_scrub import scrub_sam3_metadata
from .video_track_job_executor import Sam3VideoTrackJobExecution

SAM3_VIDEO_TRACK_JOB_STORE_RESULT_SCHEMA_VERSION = "sam3.video_track_job_store_result.v1"
Sam3VideoTrackJobStoreStage = Literal["queued", "running", "final"]

_JOB_RESOURCE_PREFIX = "loomstroke://sam3/job/"
_REDACTED = "<redacted>"
_SOURCE = "sam3_video_track_job_store"
_STAGES: tuple[Sam3VideoTrackJobStoreStage, ...] = ("queued", "running", "final")


class Sam3VideoTrackJobStoreError(ValueError):
    """Raised when a SAM3 video track job status cannot be stored."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def save_video_track_job_status(
    asset_store: Sam3AssetStore,
    status: Sam3JobStatus,
    *,
    stage: Sam3VideoTrackJobStoreStage | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Persist a video track job status in the existing SAM3 asset store."""

    store = _asset_store(asset_store)
    job_status = _track_video_status(status)
    resolved_stage = _resolve_stage(stage, job_status)
    safe_status = _safe_status(job_status)
    try:
        path = store.save_job_status(safe_status)
    except (OSError, TypeError, ValueError):
        raise Sam3VideoTrackJobStoreError(
            "video_track_job_store_write_failed",
            "SAM3 video track job status could not be saved.",
        ) from None

    return _store_result(safe_status, resolved_stage, path, diagnostics)


def save_video_track_job_execution(
    asset_store: Sam3AssetStore,
    execution: Sam3VideoTrackJobExecution,
    *,
    stage: Sam3VideoTrackJobStoreStage = "final",
    diagnostics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Persist one status projection from a video track job execution."""

    job_execution = _job_execution(execution)
    resolved_stage = _stage(stage)
    status_by_stage: dict[Sam3VideoTrackJobStoreStage, Sam3JobStatus] = {
        "queued": job_execution.queued_status,
        "running": job_execution.running_status,
        "final": job_execution.final_status,
    }
    return save_video_track_job_status(
        asset_store,
        status_by_stage[resolved_stage],
        stage=resolved_stage,
        diagnostics=_execution_diagnostics(job_execution, resolved_stage, diagnostics),
    )


def _asset_store(value: object) -> Sam3AssetStore:
    if not isinstance(value, Sam3AssetStore):
        raise Sam3VideoTrackJobStoreError(
            "video_track_job_store_asset_store_invalid",
            "SAM3 video track job store requires a SAM3 asset store.",
        )
    return value


def _track_video_status(value: object) -> Sam3JobStatus:
    if not isinstance(value, Sam3JobStatus):
        raise Sam3VideoTrackJobStoreError(
            "video_track_job_store_status_invalid",
            "SAM3 video track job store requires a job status.",
        )
    if value.kind != "track_video":
        raise Sam3VideoTrackJobStoreError(
            "video_track_job_store_kind_invalid",
            "SAM3 video track job store only accepts track_video statuses.",
        )
    return value


def _job_execution(value: object) -> Sam3VideoTrackJobExecution:
    if not isinstance(value, Sam3VideoTrackJobExecution):
        raise Sam3VideoTrackJobStoreError(
            "video_track_job_store_execution_invalid",
            "SAM3 video track job store requires a video track job execution.",
        )
    return value


def _safe_status(status: Sam3JobStatus) -> Sam3JobStatus:
    return replace(status, diagnostics=_scrubbed_mapping(status.diagnostics))


def _resolve_stage(
    stage: Sam3VideoTrackJobStoreStage | None,
    status: Sam3JobStatus,
) -> Sam3VideoTrackJobStoreStage:
    if stage is None:
        if status.status in {"queued", "running"} and not status.terminal:
            return cast(Sam3VideoTrackJobStoreStage, status.status)
        if status.terminal and status.status not in {"queued", "running"}:
            return "final"
        raise Sam3VideoTrackJobStoreError(
            "video_track_job_store_stage_status_mismatch",
            "SAM3 video track job status stage does not match terminal state.",
        )

    resolved_stage = _stage(stage)
    _validate_stage_status(resolved_stage, status)
    return resolved_stage


def _stage(value: object) -> Sam3VideoTrackJobStoreStage:
    text = str(value or "").strip()
    if text not in _STAGES:
        raise Sam3VideoTrackJobStoreError(
            "video_track_job_store_stage_invalid",
            "SAM3 video track job store stage must be queued, running, or final.",
        )
    return cast(Sam3VideoTrackJobStoreStage, text)


def _validate_stage_status(
    stage: Sam3VideoTrackJobStoreStage,
    status: Sam3JobStatus,
) -> None:
    if stage in {"queued", "running"}:
        if status.status == stage and not status.terminal:
            return
        raise Sam3VideoTrackJobStoreError(
            "video_track_job_store_stage_status_mismatch",
            "SAM3 video track live job stage requires a matching non-terminal status.",
        )

    if status.terminal and status.status not in {"queued", "running"}:
        return
    raise Sam3VideoTrackJobStoreError(
        "video_track_job_store_stage_status_mismatch",
        "SAM3 video track final job stage requires a terminal status.",
    )


def _store_result(
    status: Sam3JobStatus,
    stage: Sam3VideoTrackJobStoreStage,
    path: Path,
    diagnostics: Mapping[str, object] | None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_JOB_STORE_RESULT_SCHEMA_VERSION,
        "status": "saved",
        "stage": stage,
        "jobId": status.job_id,
        "jobKind": status.kind,
        "jobStatus": status.status,
        "terminal": status.terminal,
        "resourceUri": f"{_JOB_RESOURCE_PREFIX}{status.job_id}",
        "storePathName": path.name,
        "diagnostics": _result_diagnostics(status, stage, diagnostics),
    }
    return cast(dict[str, object], scrub_sam3_metadata(result))


def _result_diagnostics(
    status: Sam3JobStatus,
    stage: Sam3VideoTrackJobStoreStage,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_JOB_STORE_RESULT_SCHEMA_VERSION,
        "source": _SOURCE,
        "stage": stage,
        "jobStatus": status.status,
        "terminal": status.terminal,
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _execution_diagnostics(
    execution: Sam3VideoTrackJobExecution,
    stage: Sam3VideoTrackJobStoreStage,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_JOB_STORE_RESULT_SCHEMA_VERSION,
        "source": _SOURCE,
        "storeInput": "video_track_job_execution",
        "stage": stage,
        "jobId": execution.job_id,
        "runStatus": execution.run_result.status,
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


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
    "SAM3_VIDEO_TRACK_JOB_STORE_RESULT_SCHEMA_VERSION",
    "Sam3VideoTrackJobStoreError",
    "Sam3VideoTrackJobStoreStage",
    "save_video_track_job_execution",
    "save_video_track_job_status",
]
