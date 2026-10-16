"""Persisted cancel helpers for SAM3 video track jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from .assets import Sam3AssetStore
from .data_model import Sam3JobStatus
from .metadata_scrub import scrub_sam3_metadata
from .video_track_cancel import (
    Sam3VideoTrackCancelError,
    Sam3VideoTrackCancelResult,
    cancel_video_track_job,
)
from .video_track_job_store import Sam3VideoTrackJobStoreError, save_video_track_job_status
from .video_track_runner import Sam3VideoTrackRunner

SAM3_VIDEO_TRACK_CANCEL_STORE_RESULT_SCHEMA_VERSION = "sam3.video_track_cancel_store_result.v1"

_REDACTED = "<redacted>"
_SOURCE = "sam3_video_track_cancel_store"


class Sam3VideoTrackCancelStoreError(ValueError):
    """Raised when a SAM3 video track cancel result cannot be stored."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackCancelStoreResult:
    cancel_result: Sam3VideoTrackCancelResult
    job_id: str
    job_status: Sam3JobStatus | None = None
    store_result: Mapping[str, object] | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_CANCEL_STORE_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        cancel_result = _cancel_result(self.cancel_result)
        job_id = _request_id(self.job_id)
        job_status = _optional_job_status(self.job_status)
        store_result = _optional_store_result(self.store_result)
        object.__setattr__(self, "cancel_result", cancel_result)
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "job_status", job_status)
        object.__setattr__(self, "store_result", store_result)
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    @property
    def cancelled(self) -> bool:
        return self.cancel_result.cancelled

    @property
    def stored(self) -> bool:
        return self.store_result is not None and self.job_status is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "requestId": self.cancel_result.request_id,
            "jobId": self.job_id,
            "cancelled": self.cancelled,
            "stored": self.stored,
            "cancelResult": self.cancel_result.to_dict(),
            "jobStatus": self.job_status.to_dict() if self.job_status is not None else None,
            "storeResult": dict(self.store_result) if self.store_result is not None else None,
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackCancelStoreResult:
        job_status_payload = value.get("jobStatus")
        return cls(
            cancel_result=Sam3VideoTrackCancelResult.from_dict(
                _raw_dict(value.get("cancelResult"))
            ),
            job_id=str(value.get("jobId", "")),
            job_status=(
                Sam3JobStatus.from_dict(_raw_dict(job_status_payload))
                if isinstance(job_status_payload, Mapping)
                else None
            ),
            store_result=(
                _raw_dict(value.get("storeResult"))
                if isinstance(value.get("storeResult"), Mapping)
                else None
            ),
            diagnostics=_scrubbed_mapping(_raw_dict(value.get("diagnostics"))),
            schema_version=str(
                value.get(
                    "schemaVersion",
                    SAM3_VIDEO_TRACK_CANCEL_STORE_RESULT_SCHEMA_VERSION,
                )
            ),
        )


async def cancel_and_store_video_track_job(
    runner: Sam3VideoTrackRunner,
    request_id: str,
    asset_store: Sam3AssetStore,
    *,
    job_id: str | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoTrackCancelStoreResult:
    """Cancel a runner job and persist a cancelled job status when acknowledged."""

    track_runner = _runner(runner)
    normalized_request_id = _request_id(request_id)
    store = _asset_store(asset_store)
    resolved_job_id = _request_id(job_id or normalized_request_id)
    try:
        cancel_result = await cancel_video_track_job(
            track_runner,
            normalized_request_id,
            diagnostics=_cancel_diagnostics(resolved_job_id, diagnostics),
        )
    except Sam3VideoTrackCancelError as exc:
        raise Sam3VideoTrackCancelStoreError(_mapped_cancel_error_code(exc.code), str(exc)) from exc

    if not cancel_result.cancelled:
        return Sam3VideoTrackCancelStoreResult(
            cancel_result=cancel_result,
            job_id=resolved_job_id,
            diagnostics=_result_diagnostics(cancel_result, resolved_job_id, False, diagnostics),
        )

    job_status = _cancelled_job_status(cancel_result, resolved_job_id, diagnostics)
    store_result = _save_cancelled_status(
        store,
        job_status,
        diagnostics=_store_diagnostics(cancel_result, diagnostics),
    )
    return Sam3VideoTrackCancelStoreResult(
        cancel_result=cancel_result,
        job_id=resolved_job_id,
        job_status=job_status,
        store_result=store_result,
        diagnostics=_result_diagnostics(cancel_result, resolved_job_id, True, diagnostics),
    )


def _runner(value: object) -> Sam3VideoTrackRunner:
    if not callable(getattr(value, "cancel", None)):
        raise Sam3VideoTrackCancelStoreError(
            "video_track_cancel_store_runner_invalid",
            "SAM3 video track cancel store requires a runner with a cancel method.",
        )
    return cast(Sam3VideoTrackRunner, value)


def _asset_store(value: object) -> Sam3AssetStore:
    if not isinstance(value, Sam3AssetStore):
        raise Sam3VideoTrackCancelStoreError(
            "video_track_cancel_store_asset_store_invalid",
            "SAM3 video track cancel store requires a SAM3 asset store.",
        )
    return value


def _request_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoTrackCancelStoreError(
            "video_track_cancel_store_request_id_missing",
            "SAM3 video track cancel store requires a request id.",
        )
    return text


def _cancel_result(value: object) -> Sam3VideoTrackCancelResult:
    if not isinstance(value, Sam3VideoTrackCancelResult):
        raise Sam3VideoTrackCancelStoreError(
            "video_track_cancel_store_result_invalid",
            "SAM3 video track cancel store requires a cancel result.",
        )
    return value


def _optional_job_status(value: object) -> Sam3JobStatus | None:
    if value is None:
        return None
    if not isinstance(value, Sam3JobStatus):
        raise Sam3VideoTrackCancelStoreError(
            "video_track_cancel_store_job_status_invalid",
            "SAM3 video track cancel store requires a job status.",
        )
    return value


def _optional_store_result(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise Sam3VideoTrackCancelStoreError(
            "video_track_cancel_store_store_result_invalid",
            "SAM3 video track cancel store requires a store result.",
        )
    return _scrubbed_mapping(value)


def _cancelled_job_status(
    cancel_result: Sam3VideoTrackCancelResult,
    job_id: str,
    extra: Mapping[str, object] | None,
) -> Sam3JobStatus:
    return Sam3JobStatus(
        job_id=job_id,
        kind="track_video",
        status="cancelled",
        terminal=True,
        recommended_actions=tuple(cancel_result.recommended_actions),
        diagnostics=_job_status_diagnostics(cancel_result, extra),
    )


def _save_cancelled_status(
    asset_store: Sam3AssetStore,
    status: Sam3JobStatus,
    *,
    diagnostics: Mapping[str, object],
) -> dict[str, object]:
    try:
        return save_video_track_job_status(
            asset_store,
            status,
            stage="final",
            diagnostics=diagnostics,
        )
    except Sam3VideoTrackJobStoreError as exc:
        raise Sam3VideoTrackCancelStoreError(exc.code, str(exc)) from exc


def _mapped_cancel_error_code(code: str) -> str:
    if code == "video_track_cancel_runner_invalid":
        return "video_track_cancel_store_runner_invalid"
    if code == "video_track_cancel_request_id_missing":
        return "video_track_cancel_store_request_id_missing"
    return code


def _cancel_diagnostics(
    job_id: str,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "source": _SOURCE,
        "jobId": job_id,
        "operation": "cancel",
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _job_status_diagnostics(
    cancel_result: Sam3VideoTrackCancelResult,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_CANCEL_STORE_RESULT_SCHEMA_VERSION,
        "source": _SOURCE,
        "requestId": cancel_result.request_id,
        "cancelStatus": cancel_result.status,
        "cancelResult": cancel_result.to_dict(),
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _store_diagnostics(
    cancel_result: Sam3VideoTrackCancelResult,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_CANCEL_STORE_RESULT_SCHEMA_VERSION,
        "source": _SOURCE,
        "requestId": cancel_result.request_id,
        "cancelStatus": cancel_result.status,
        "storeStage": "final",
    }
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _result_diagnostics(
    cancel_result: Sam3VideoTrackCancelResult,
    job_id: str,
    stored: bool,
    extra: Mapping[str, object] | None,
) -> dict[str, Any]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_CANCEL_STORE_RESULT_SCHEMA_VERSION,
        "source": _SOURCE,
        "requestId": cancel_result.request_id,
        "jobId": job_id,
        "cancelStatus": cancel_result.status,
        "stored": stored,
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
    "SAM3_VIDEO_TRACK_CANCEL_STORE_RESULT_SCHEMA_VERSION",
    "Sam3VideoTrackCancelStoreError",
    "Sam3VideoTrackCancelStoreResult",
    "cancel_and_store_video_track_job",
]
