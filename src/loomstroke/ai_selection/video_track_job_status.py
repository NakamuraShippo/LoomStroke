"""Job status projections for SAM3 video tracking."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Literal, cast

from .data_model import JobState, Sam3JobStatus
from .metadata_scrub import scrub_sam3_metadata
from .video_track_request import Sam3VideoTrackRequest
from .video_track_run import Sam3VideoTrackRunResult

SAM3_VIDEO_TRACK_JOB_STATUS_PROJECTION_SCHEMA_VERSION = "sam3.video_track_job_status_projection.v1"

Sam3VideoTrackLiveJobState = Literal["queued", "running"]
_LIVE_JOB_STATES: tuple[Sam3VideoTrackLiveJobState, ...] = ("queued", "running")
_REDACTED = "<redacted>"


class Sam3VideoTrackJobStatusError(ValueError):
    """Raised when a SAM3 video track job status projection cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_video_track_request_job_status(
    request: Sam3VideoTrackRequest,
    *,
    status: Sam3VideoTrackLiveJobState = "queued",
    job_id: str | None = None,
    progress: float | None = None,
    waited_ms: int | None = None,
    timed_out: bool = False,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3JobStatus:
    """Project a video track request to a non-terminal SAM3 job status."""

    track_request = _track_request(request)
    live_status = _live_status(status)
    return Sam3JobStatus(
        job_id=_job_id(job_id, fallback=track_request.request_id),
        kind="track_video",
        status=cast(JobState, live_status),
        progress=_progress(progress, fallback=0.0),
        terminal=False,
        timed_out=bool(timed_out),
        waited_ms=_optional_non_negative_int(waited_ms),
        diagnostics=_request_diagnostics(track_request, live_status, diagnostics),
    )


def build_video_track_run_job_status(
    run_result: Sam3VideoTrackRunResult,
    *,
    job_id: str | None = None,
    progress: float | None = None,
    timed_out: bool = False,
    waited_ms: int | None = None,
    output_asset_ids: Sequence[str] = (),
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3JobStatus:
    """Project a terminal video track run result to a SAM3 job status."""

    result = _run_result(run_result)
    explicit_output_asset_ids = _asset_ids(output_asset_ids)
    return Sam3JobStatus(
        job_id=_job_id(job_id, fallback=result.request.request_id),
        kind="track_video",
        status=cast(JobState, result.status),
        progress=_progress(progress, fallback=_run_progress(result)),
        terminal=True,
        timed_out=bool(timed_out),
        waited_ms=_optional_non_negative_int(waited_ms),
        output_asset_ids=explicit_output_asset_ids or _track_result_asset_ids(result),
        blocked_reasons=tuple(result.blocked_reasons),
        recommended_actions=tuple(result.recommended_actions),
        diagnostics=_run_diagnostics(result, diagnostics),
    )


def _track_request(value: object) -> Sam3VideoTrackRequest:
    if not isinstance(value, Sam3VideoTrackRequest):
        raise Sam3VideoTrackJobStatusError(
            "video_track_job_status_request_invalid",
            "SAM3 video track job status projection requires a video track request.",
        )
    return value


def _run_result(value: object) -> Sam3VideoTrackRunResult:
    if not isinstance(value, Sam3VideoTrackRunResult):
        raise Sam3VideoTrackJobStatusError(
            "video_track_job_status_run_result_invalid",
            "SAM3 video track job status projection requires a video track run result.",
        )
    return value


def _live_status(value: object) -> Sam3VideoTrackLiveJobState:
    text = str(value or "").strip()
    if text not in _LIVE_JOB_STATES:
        raise Sam3VideoTrackJobStatusError(
            "video_track_job_status_state_invalid",
            "SAM3 video track request job status must be queued or running.",
        )
    return cast(Sam3VideoTrackLiveJobState, text)


def _job_id(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _progress(value: object, *, fallback: float) -> float:
    if value is None:
        number = fallback
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = fallback
    if not math.isfinite(number):
        number = fallback
    return min(1.0, max(0.0, number))


def _run_progress(run_result: Sam3VideoTrackRunResult) -> float:
    if run_result.completed:
        return 1.0
    frame_count = max(1, len(run_result.request.source_manifest.frames))
    return _progress(run_result.partial_frame_count / frame_count, fallback=0.0)


def _optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _track_result_asset_ids(run_result: Sam3VideoTrackRunResult) -> tuple[str, ...]:
    track_result = run_result.track_result
    if track_result is None:
        return ()

    asset_ids: list[str] = []
    for frame in track_result.frames:
        asset_ids.extend(str(asset_id) for asset_id in frame.object_masks.values())
        if frame.preview_asset_id:
            asset_ids.append(frame.preview_asset_id)
    return _asset_ids(asset_ids)


def _asset_ids(values: Sequence[str]) -> tuple[str, ...]:
    raw_values: Sequence[object] = (
        (values,) if isinstance(values, (str, bytes, bytearray)) else values
    )

    seen: set[str] = set()
    asset_ids: list[str] = []
    for value in raw_values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        asset_ids.append(text)
    return tuple(asset_ids)


def _request_diagnostics(
    request: Sam3VideoTrackRequest,
    status: Sam3VideoTrackLiveJobState,
    extra: Mapping[str, object] | None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_JOB_STATUS_PROJECTION_SCHEMA_VERSION,
        "projection": "request",
        "jobStatus": status,
        "requestId": request.request_id,
        "sourceManifestId": request.source_manifest.manifest_id,
        "sourceKind": request.source_manifest.source_kind,
        "sourceAssetId": request.source_manifest.source_asset_id,
        "frameCount": len(request.source_manifest.frames),
        "objectCount": len(request.objects),
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _run_diagnostics(
    run_result: Sam3VideoTrackRunResult,
    extra: Mapping[str, object] | None,
) -> dict[str, object]:
    track_result = run_result.track_result
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_JOB_STATUS_PROJECTION_SCHEMA_VERSION,
        "projection": "run_result",
        "runStatus": run_result.status,
        "requestId": run_result.request.request_id,
        "sourceManifestId": run_result.request.source_manifest.manifest_id,
        "sourceKind": run_result.request.source_manifest.source_kind,
        "sourceAssetId": run_result.request.source_manifest.source_asset_id,
        "frameCount": len(run_result.request.source_manifest.frames),
        "partialFrameCount": run_result.partial_frame_count,
        "trackId": track_result.track_id if track_result is not None else None,
        "runDiagnostics": dict(run_result.diagnostics),
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _scrubbed_mapping(value: Mapping[str, object]) -> dict[str, object]:
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
    "SAM3_VIDEO_TRACK_JOB_STATUS_PROJECTION_SCHEMA_VERSION",
    "Sam3VideoTrackJobStatusError",
    "Sam3VideoTrackLiveJobState",
    "build_video_track_request_job_status",
    "build_video_track_run_job_status",
]
