"""Persisted orchestration for SAM3 video track job executions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from .assets import Sam3AssetStore
from .data_model import Sam3JobStatus
from .metadata_scrub import scrub_sam3_metadata
from .video_track_job_executor import Sam3VideoTrackJobExecution, run_video_track_job
from .video_track_job_status import build_video_track_request_job_status
from .video_track_job_store import (
    Sam3VideoTrackJobStoreError,
    Sam3VideoTrackJobStoreStage,
    save_video_track_job_execution,
    save_video_track_job_status,
)
from .video_track_request import Sam3VideoTrackRequest
from .video_track_runner import Sam3VideoTrackRunner

SAM3_VIDEO_TRACK_STORED_JOB_EXECUTION_SCHEMA_VERSION = "sam3.video_track_stored_job_execution.v1"

_REDACTED = "<redacted>"
_SOURCE = "sam3_video_track_stored_job_executor"


class Sam3VideoTrackStoredJobExecutionError(ValueError):
    """Raised when a SAM3 video track stored job execution cannot proceed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackStoredJobExecution:
    execution: Sam3VideoTrackJobExecution
    queued_store_result: Mapping[str, object]
    running_store_result: Mapping[str, object]
    final_store_result: Mapping[str, object]
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_STORED_JOB_EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        execution = _job_execution(self.execution)
        object.__setattr__(self, "execution", execution)
        object.__setattr__(
            self,
            "queued_store_result",
            _store_result(self.queued_store_result, "queued_store_result"),
        )
        object.__setattr__(
            self,
            "running_store_result",
            _store_result(self.running_store_result, "running_store_result"),
        )
        object.__setattr__(
            self,
            "final_store_result",
            _store_result(self.final_store_result, "final_store_result"),
        )
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    @property
    def job_id(self) -> str:
        return self.execution.job_id

    @property
    def completed(self) -> bool:
        return self.execution.completed

    @property
    def terminal(self) -> bool:
        return self.execution.terminal

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "jobId": self.job_id,
            "runStatus": self.execution.run_result.status,
            "completed": self.completed,
            "terminal": self.terminal,
            "execution": self.execution.to_dict(),
            "queuedStoreResult": dict(self.queued_store_result),
            "runningStoreResult": dict(self.running_store_result),
            "finalStoreResult": dict(self.final_store_result),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackStoredJobExecution:
        return cls(
            execution=Sam3VideoTrackJobExecution.from_dict(_raw_dict(value.get("execution"))),
            queued_store_result=_raw_dict(value.get("queuedStoreResult")),
            running_store_result=_raw_dict(value.get("runningStoreResult")),
            final_store_result=_raw_dict(value.get("finalStoreResult")),
            diagnostics=_scrubbed_mapping(_raw_dict(value.get("diagnostics"))),
            schema_version=str(
                value.get(
                    "schemaVersion",
                    SAM3_VIDEO_TRACK_STORED_JOB_EXECUTION_SCHEMA_VERSION,
                )
            ),
        )


async def run_and_store_video_track_job(
    runner: Sam3VideoTrackRunner,
    request: Sam3VideoTrackRequest,
    asset_store: Sam3AssetStore,
    *,
    job_id: str | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoTrackStoredJobExecution:
    """Run a video track request while persisting queued/running/final status."""

    track_runner = _runner(runner)
    track_request = _track_request(request)
    store = _asset_store(asset_store)
    resolved_job_id = _job_id(job_id, fallback=track_request.request_id)
    queued_status = build_video_track_request_job_status(
        track_request,
        status="queued",
        job_id=resolved_job_id,
        diagnostics=_stage_diagnostics("queued", diagnostics),
    )
    queued_store_result = _save_job_status(
        store,
        queued_status,
        stage="queued",
        diagnostics=_store_diagnostics("queued", diagnostics),
    )
    running_status = build_video_track_request_job_status(
        track_request,
        status="running",
        job_id=resolved_job_id,
        diagnostics=_stage_diagnostics("running", diagnostics),
    )
    running_store_result = _save_job_status(
        store,
        running_status,
        stage="running",
        diagnostics=_store_diagnostics("running", diagnostics),
    )
    execution = await run_video_track_job(
        track_runner,
        track_request,
        job_id=resolved_job_id,
        diagnostics=_stage_diagnostics("execution", diagnostics),
    )
    final_store_result = _save_job_execution(
        store,
        execution,
        stage="final",
        diagnostics=_store_diagnostics("final", diagnostics),
    )
    return Sam3VideoTrackStoredJobExecution(
        execution=execution,
        queued_store_result=queued_store_result,
        running_store_result=running_store_result,
        final_store_result=final_store_result,
        diagnostics=_execution_diagnostics(execution, diagnostics),
    )


def _runner(value: object) -> Sam3VideoTrackRunner:
    if not callable(getattr(value, "track", None)):
        raise Sam3VideoTrackStoredJobExecutionError(
            "video_track_stored_job_runner_invalid",
            "SAM3 video track stored job executor requires a runner with a track method.",
        )
    return cast(Sam3VideoTrackRunner, value)


def _track_request(value: object) -> Sam3VideoTrackRequest:
    if not isinstance(value, Sam3VideoTrackRequest):
        raise Sam3VideoTrackStoredJobExecutionError(
            "video_track_stored_job_request_invalid",
            "SAM3 video track stored job executor requires a video track request.",
        )
    return value


def _asset_store(value: object) -> Sam3AssetStore:
    if not isinstance(value, Sam3AssetStore):
        raise Sam3VideoTrackStoredJobExecutionError(
            "video_track_stored_job_asset_store_invalid",
            "SAM3 video track stored job executor requires a SAM3 asset store.",
        )
    return value


def _job_execution(value: object) -> Sam3VideoTrackJobExecution:
    if not isinstance(value, Sam3VideoTrackJobExecution):
        raise Sam3VideoTrackStoredJobExecutionError(
            "video_track_stored_job_execution_invalid",
            "SAM3 video track stored job executor requires a video track job execution.",
        )
    return value


def _store_result(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Sam3VideoTrackStoredJobExecutionError(
            "video_track_stored_job_store_result_invalid",
            f"SAM3 video track stored job execution requires {field_name}.",
        )
    return _scrubbed_mapping(value)


def _save_job_status(
    asset_store: Sam3AssetStore,
    status: Sam3JobStatus,
    *,
    stage: Sam3VideoTrackJobStoreStage,
    diagnostics: Mapping[str, object],
) -> dict[str, object]:
    try:
        return save_video_track_job_status(
            asset_store,
            status,
            stage=stage,
            diagnostics=diagnostics,
        )
    except Sam3VideoTrackJobStoreError as exc:
        raise Sam3VideoTrackStoredJobExecutionError(exc.code, str(exc)) from exc


def _save_job_execution(
    asset_store: Sam3AssetStore,
    execution: Sam3VideoTrackJobExecution,
    *,
    stage: Sam3VideoTrackJobStoreStage,
    diagnostics: Mapping[str, object],
) -> dict[str, object]:
    try:
        return save_video_track_job_execution(
            asset_store,
            execution,
            stage=stage,
            diagnostics=diagnostics,
        )
    except Sam3VideoTrackJobStoreError as exc:
        raise Sam3VideoTrackStoredJobExecutionError(exc.code, str(exc)) from exc


def _job_id(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _stage_diagnostics(
    stage: str,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "source": _SOURCE,
        "executorStage": stage,
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _store_diagnostics(
    stage: str,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "source": _SOURCE,
        "storeStage": stage,
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _execution_diagnostics(
    execution: Sam3VideoTrackJobExecution,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_STORED_JOB_EXECUTION_SCHEMA_VERSION,
        "source": _SOURCE,
        "jobId": execution.job_id,
        "runStatus": execution.run_result.status,
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _raw_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


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
    "SAM3_VIDEO_TRACK_STORED_JOB_EXECUTION_SCHEMA_VERSION",
    "Sam3VideoTrackStoredJobExecution",
    "Sam3VideoTrackStoredJobExecutionError",
    "run_and_store_video_track_job",
]
