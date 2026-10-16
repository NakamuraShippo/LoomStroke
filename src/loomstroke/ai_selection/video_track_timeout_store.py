"""Timeout status store helpers for SAM3 video track jobs."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .assets import Sam3AssetStore
from .data_model import Sam3JobStatus
from .metadata_scrub import scrub_sam3_metadata
from .video_track_job_store import Sam3VideoTrackJobStoreError, save_video_track_job_status
from .video_track_request import Sam3VideoTrackRequest

SAM3_VIDEO_TRACK_TIMEOUT_STORE_RESULT_SCHEMA_VERSION = "sam3.video_track_timeout_store_result.v1"

_REDACTED = "<redacted>"
_SOURCE = "sam3_video_track_timeout_store"
_DEFAULT_ACTIONS = ("retry_video_track_job",)


class Sam3VideoTrackTimeoutStoreError(ValueError):
    """Raised when a SAM3 video track timeout status cannot be stored."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackTimeoutStoreResult:
    job_status: Sam3JobStatus
    store_result: Mapping[str, object]
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_TIMEOUT_STORE_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_status", _timeout_job_status(self.job_status))
        object.__setattr__(self, "store_result", _store_result(self.store_result))
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    @property
    def job_id(self) -> str:
        return self.job_status.job_id

    @property
    def timed_out(self) -> bool:
        return self.job_status.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "jobId": self.job_id,
            "timedOut": self.timed_out,
            "waitedMs": self.job_status.waited_ms,
            "jobStatus": self.job_status.to_dict(),
            "storeResult": dict(self.store_result),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackTimeoutStoreResult:
        return cls(
            job_status=Sam3JobStatus.from_dict(_raw_dict(value.get("jobStatus"))),
            store_result=_raw_dict(value.get("storeResult")),
            diagnostics=_scrubbed_mapping(_raw_dict(value.get("diagnostics"))),
            schema_version=str(
                value.get(
                    "schemaVersion",
                    SAM3_VIDEO_TRACK_TIMEOUT_STORE_RESULT_SCHEMA_VERSION,
                )
            ),
        )


def save_video_track_timeout_status(
    request: Sam3VideoTrackRequest,
    asset_store: Sam3AssetStore,
    *,
    job_id: str | None = None,
    waited_ms: int | None = None,
    progress: float | None = None,
    recommended_actions: Sequence[str] = _DEFAULT_ACTIONS,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoTrackTimeoutStoreResult:
    """Persist a terminal timed-out status for a SAM3 video track request."""

    track_request = _track_request(request)
    store = _asset_store(asset_store)
    resolved_job_id = _job_id(job_id, fallback=track_request.request_id)
    normalized_waited_ms = _non_negative_int(waited_ms)
    status = Sam3JobStatus(
        job_id=resolved_job_id,
        kind="track_video",
        status="failed",
        progress=_progress(progress),
        terminal=True,
        timed_out=True,
        waited_ms=normalized_waited_ms,
        recommended_actions=_recommended_actions(recommended_actions),
        diagnostics=_timeout_status_diagnostics(
            track_request,
            resolved_job_id,
            normalized_waited_ms,
            diagnostics,
        ),
    )
    try:
        store_result = save_video_track_job_status(
            store,
            status,
            stage="final",
            diagnostics=_store_diagnostics(track_request, resolved_job_id, diagnostics),
        )
    except Sam3VideoTrackJobStoreError as exc:
        raise Sam3VideoTrackTimeoutStoreError(exc.code, str(exc)) from exc

    return Sam3VideoTrackTimeoutStoreResult(
        job_status=status,
        store_result=store_result,
        diagnostics=_result_diagnostics(status, diagnostics),
    )


def _track_request(value: object) -> Sam3VideoTrackRequest:
    if not isinstance(value, Sam3VideoTrackRequest):
        raise Sam3VideoTrackTimeoutStoreError(
            "video_track_timeout_store_request_invalid",
            "SAM3 video track timeout store requires a video track request.",
        )
    return value


def _asset_store(value: object) -> Sam3AssetStore:
    if not isinstance(value, Sam3AssetStore):
        raise Sam3VideoTrackTimeoutStoreError(
            "video_track_timeout_store_asset_store_invalid",
            "SAM3 video track timeout store requires a SAM3 asset store.",
        )
    return value


def _timeout_job_status(value: object) -> Sam3JobStatus:
    if not isinstance(value, Sam3JobStatus):
        raise Sam3VideoTrackTimeoutStoreError(
            "video_track_timeout_store_status_invalid",
            "SAM3 video track timeout store result requires a job status.",
        )
    if (
        value.kind != "track_video"
        or value.status != "failed"
        or not value.terminal
        or not value.timed_out
    ):
        raise Sam3VideoTrackTimeoutStoreError(
            "video_track_timeout_store_status_invalid",
            "SAM3 video track timeout store result requires a terminal timed-out status.",
        )
    return value


def _store_result(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Sam3VideoTrackTimeoutStoreError(
            "video_track_timeout_store_result_invalid",
            "SAM3 video track timeout store result requires a store result.",
        )
    return _scrubbed_mapping(value)


def _job_id(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _progress(value: object) -> float:
    try:
        number = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    return min(1.0, max(0.0, number))


def _recommended_actions(values: Sequence[str]) -> tuple[str, ...]:
    raw_values: Sequence[object] = (
        (values,) if isinstance(values, (str, bytes, bytearray)) else values
    )
    actions = tuple(str(action) for action in raw_values if str(action).strip())
    return actions or _DEFAULT_ACTIONS


def _timeout_status_diagnostics(
    request: Sam3VideoTrackRequest,
    job_id: str,
    waited_ms: int,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_TIMEOUT_STORE_RESULT_SCHEMA_VERSION,
        "source": _SOURCE,
        "jobId": job_id,
        "requestId": request.request_id,
        "sourceManifestId": request.source_manifest.manifest_id,
        "sourceKind": request.source_manifest.source_kind,
        "sourceAssetId": request.source_manifest.source_asset_id,
        "frameCount": len(request.source_manifest.frames),
        "objectCount": len(request.objects),
        "timedOut": True,
        "waitedMs": waited_ms,
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _store_diagnostics(
    request: Sam3VideoTrackRequest,
    job_id: str,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_TIMEOUT_STORE_RESULT_SCHEMA_VERSION,
        "source": _SOURCE,
        "jobId": job_id,
        "requestId": request.request_id,
        "storeStage": "final",
        "operation": "timeout",
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _result_diagnostics(
    status: Sam3JobStatus,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_TIMEOUT_STORE_RESULT_SCHEMA_VERSION,
        "source": _SOURCE,
        "jobId": status.job_id,
        "jobStatus": status.status,
        "timedOut": status.timed_out,
        "waitedMs": status.waited_ms,
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
    "SAM3_VIDEO_TRACK_TIMEOUT_STORE_RESULT_SCHEMA_VERSION",
    "Sam3VideoTrackTimeoutStoreError",
    "Sam3VideoTrackTimeoutStoreResult",
    "save_video_track_timeout_status",
]
