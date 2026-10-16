"""Async job executor for SAM3 video tracking runners."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from .data_model import Sam3JobStatus, Sam3Warning
from .metadata_scrub import scrub_sam3_metadata
from .video_track_job_status import (
    build_video_track_request_job_status,
    build_video_track_run_job_status,
)
from .video_track_request import Sam3VideoTrackRequest
from .video_track_run import Sam3VideoTrackRunResult, build_video_track_run_result
from .video_track_runner import Sam3VideoTrackRunner

SAM3_VIDEO_TRACK_JOB_EXECUTION_SCHEMA_VERSION = "sam3.video_track_job_execution.v1"
_EXECUTOR_SOURCE = "sam3_video_track_job_executor"
_REDACTED = "<redacted>"


class Sam3VideoTrackJobExecutionError(ValueError):
    """Raised when a SAM3 video track job execution cannot be represented."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackJobExecution:
    job_id: str
    queued_status: Sam3JobStatus
    running_status: Sam3JobStatus
    final_status: Sam3JobStatus
    run_result: Sam3VideoTrackRunResult
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_JOB_EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        run_result = _run_result(self.run_result)
        job_id = _job_id(self.job_id, fallback=run_result.request.request_id)
        queued_status = _job_status(self.queued_status, "queued_status")
        running_status = _job_status(self.running_status, "running_status")
        final_status = _job_status(self.final_status, "final_status")
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "queued_status", queued_status)
        object.__setattr__(self, "running_status", running_status)
        object.__setattr__(self, "final_status", final_status)
        object.__setattr__(self, "run_result", run_result)
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    @property
    def completed(self) -> bool:
        return self.run_result.completed

    @property
    def terminal(self) -> bool:
        return self.final_status.terminal

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "jobId": self.job_id,
            "runStatus": self.run_result.status,
            "completed": self.completed,
            "terminal": self.terminal,
            "queuedStatus": self.queued_status.to_dict(),
            "runningStatus": self.running_status.to_dict(),
            "finalStatus": self.final_status.to_dict(),
            "runResult": self.run_result.to_dict(),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackJobExecution:
        return cls(
            job_id=str(value.get("jobId", "")),
            queued_status=Sam3JobStatus.from_dict(_raw_dict(value.get("queuedStatus"))),
            running_status=Sam3JobStatus.from_dict(_raw_dict(value.get("runningStatus"))),
            final_status=Sam3JobStatus.from_dict(_raw_dict(value.get("finalStatus"))),
            run_result=Sam3VideoTrackRunResult.from_dict(_raw_dict(value.get("runResult"))),
            diagnostics=_scrubbed_mapping(_raw_dict(value.get("diagnostics"))),
            schema_version=str(
                value.get("schemaVersion", SAM3_VIDEO_TRACK_JOB_EXECUTION_SCHEMA_VERSION)
            ),
        )


async def run_video_track_job(
    runner: Sam3VideoTrackRunner,
    request: Sam3VideoTrackRequest,
    *,
    job_id: str | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoTrackJobExecution:
    """Run a SAM3 video track request and return status projections for the job."""

    track_runner = _runner(runner)
    track_request = _track_request(request)
    resolved_job_id = _job_id(job_id, fallback=track_request.request_id)
    queued_status = build_video_track_request_job_status(
        track_request,
        status="queued",
        job_id=resolved_job_id,
        diagnostics=_stage_diagnostics("queued", diagnostics),
    )
    running_status = build_video_track_request_job_status(
        track_request,
        status="running",
        job_id=resolved_job_id,
        diagnostics=_stage_diagnostics("running", diagnostics),
    )
    run_result = await _track(track_runner, track_request)
    final_status = build_video_track_run_job_status(
        run_result,
        job_id=resolved_job_id,
        diagnostics=_stage_diagnostics("final", diagnostics),
    )
    return Sam3VideoTrackJobExecution(
        job_id=resolved_job_id,
        queued_status=queued_status,
        running_status=running_status,
        final_status=final_status,
        run_result=run_result,
        diagnostics=_execution_diagnostics(run_result, diagnostics),
    )


def _runner(value: object) -> Sam3VideoTrackRunner:
    if not callable(getattr(value, "track", None)):
        raise Sam3VideoTrackJobExecutionError(
            "video_track_job_runner_invalid",
            "SAM3 video track job executor requires a runner with a track method.",
        )
    return cast(Sam3VideoTrackRunner, value)


def _track_request(value: object) -> Sam3VideoTrackRequest:
    if not isinstance(value, Sam3VideoTrackRequest):
        raise Sam3VideoTrackJobExecutionError(
            "video_track_job_request_invalid",
            "SAM3 video track job executor requires a video track request.",
        )
    return value


def _run_result(value: object) -> Sam3VideoTrackRunResult:
    if not isinstance(value, Sam3VideoTrackRunResult):
        raise Sam3VideoTrackJobExecutionError(
            "video_track_job_run_result_invalid",
            "SAM3 video track job execution requires a video track run result.",
        )
    return value


def _job_status(value: object, field_name: str) -> Sam3JobStatus:
    if not isinstance(value, Sam3JobStatus):
        raise Sam3VideoTrackJobExecutionError(
            "video_track_job_status_invalid",
            f"SAM3 video track job execution requires {field_name}.",
        )
    return value


async def _track(
    runner: Sam3VideoTrackRunner,
    request: Sam3VideoTrackRequest,
) -> Sam3VideoTrackRunResult:
    try:
        result = await runner.track(request)
    except Exception as exc:
        return _failed_result(
            request,
            warning_code="video_track_job_runner_exception",
            warning_message="SAM3 video track runner raised an exception.",
            action="retry_video_track_job",
            diagnostics={
                "source": _EXECUTOR_SOURCE,
                "failureCode": "video_track_job_runner_exception",
                "exceptionType": type(exc).__name__,
            },
        )

    if not isinstance(result, Sam3VideoTrackRunResult):
        return _failed_result(
            request,
            warning_code="video_track_job_runner_result_invalid",
            warning_message="SAM3 video track runner returned an invalid result.",
            action="fix_video_track_runner_contract",
            diagnostics={
                "source": _EXECUTOR_SOURCE,
                "failureCode": "video_track_job_runner_result_invalid",
                "returnType": type(result).__name__,
            },
        )
    return result


def _failed_result(
    request: Sam3VideoTrackRequest,
    *,
    warning_code: str,
    warning_message: str,
    action: str,
    diagnostics: Mapping[str, object],
) -> Sam3VideoTrackRunResult:
    return build_video_track_run_result(
        request=request,
        status="failed",
        warnings=(
            Sam3Warning(
                code=warning_code,
                message=warning_message,
                severity="error",
                action=action,
            ),
        ),
        recommended_actions=(action,),
        diagnostics=diagnostics,
    )


def _job_id(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _stage_diagnostics(
    stage: str,
    extra: Mapping[str, object] | None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "source": _EXECUTOR_SOURCE,
        "executorStage": stage,
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _execution_diagnostics(
    run_result: Sam3VideoTrackRunResult,
    extra: Mapping[str, object] | None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_JOB_EXECUTION_SCHEMA_VERSION,
        "source": _EXECUTOR_SOURCE,
        "runStatus": run_result.status,
        "requestId": run_result.request.request_id,
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
    "SAM3_VIDEO_TRACK_JOB_EXECUTION_SCHEMA_VERSION",
    "Sam3VideoTrackJobExecution",
    "Sam3VideoTrackJobExecutionError",
    "run_video_track_job",
]
