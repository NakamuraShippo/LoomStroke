"""Cancel result helpers for SAM3 video tracking jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .data_model import Sam3Warning
from .metadata_scrub import scrub_sam3_metadata
from .video_track_runner import Sam3VideoTrackRunner

SAM3_VIDEO_TRACK_CANCEL_RESULT_SCHEMA_VERSION = "sam3.video_track_cancel_result.v1"

Sam3VideoTrackCancelStatus = Literal["cancelled", "not_found", "failed"]
_CANCEL_STATUSES: tuple[Sam3VideoTrackCancelStatus, ...] = (
    "cancelled",
    "not_found",
    "failed",
)
_CANCEL_SOURCE = "sam3_video_track_cancel"
_REDACTED = "<redacted>"


class Sam3VideoTrackCancelError(ValueError):
    """Raised when a SAM3 video track cancel request cannot be represented."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackCancelResult:
    request_id: str
    status: Sam3VideoTrackCancelStatus
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)
    recommended_actions: tuple[str, ...] = field(default_factory=tuple)
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_CANCEL_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _request_id(self.request_id))
        object.__setattr__(self, "status", _cancel_status(self.status))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(
            self,
            "recommended_actions",
            tuple(str(action) for action in self.recommended_actions if str(action).strip()),
        )
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "requestId": self.request_id,
            "status": self.status,
            "cancelled": self.cancelled,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "recommendedActions": list(self.recommended_actions),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackCancelResult:
        return cls(
            request_id=str(value.get("requestId", "")),
            status=_cancel_status(value.get("status", "failed")),
            warnings=tuple(
                Sam3Warning.from_dict(item) for item in _dict_items(value.get("warnings"))
            ),
            recommended_actions=tuple(
                str(item) for item in _list_items(value.get("recommendedActions"))
            ),
            diagnostics=_scrubbed_mapping(_raw_dict(value.get("diagnostics"))),
            schema_version=str(
                value.get("schemaVersion", SAM3_VIDEO_TRACK_CANCEL_RESULT_SCHEMA_VERSION)
            ),
        )


async def cancel_video_track_job(
    runner: Sam3VideoTrackRunner,
    request_id: str,
    *,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoTrackCancelResult:
    """Cancel a SAM3 video track runner job and return a secret-free result."""

    track_runner = _runner(runner)
    normalized_request_id = _request_id(request_id)
    try:
        cancelled = bool(await track_runner.cancel(normalized_request_id))
    except Exception as exc:
        return _result(
            normalized_request_id,
            status="failed",
            warning_code="video_track_cancel_runner_exception",
            warning_message="SAM3 video track runner cancel raised an exception.",
            action="retry_video_track_cancel",
            diagnostics=_diagnostics(
                "failed",
                diagnostics,
                failure_code="video_track_cancel_runner_exception",
                exception_type=type(exc).__name__,
            ),
        )

    if cancelled:
        return _result(
            normalized_request_id,
            status="cancelled",
            diagnostics=_diagnostics("cancelled", diagnostics),
        )
    return _result(
        normalized_request_id,
        status="not_found",
        warning_code="video_track_cancel_not_found",
        warning_message="SAM3 video track runner did not have an active job for the request.",
        action="confirm_video_track_job",
        diagnostics=_diagnostics("not_found", diagnostics),
    )


def _runner(value: object) -> Sam3VideoTrackRunner:
    if not callable(getattr(value, "cancel", None)):
        raise Sam3VideoTrackCancelError(
            "video_track_cancel_runner_invalid",
            "SAM3 video track cancel requires a runner with a cancel method.",
        )
    return cast(Sam3VideoTrackRunner, value)


def _request_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoTrackCancelError(
            "video_track_cancel_request_id_missing",
            "SAM3 video track cancel requires a request id.",
        )
    return text


def _cancel_status(value: object) -> Sam3VideoTrackCancelStatus:
    text = str(value or "").strip()
    if text not in _CANCEL_STATUSES:
        raise Sam3VideoTrackCancelError(
            "video_track_cancel_status_invalid",
            "SAM3 video track cancel status is not supported.",
        )
    return cast(Sam3VideoTrackCancelStatus, text)


def _result(
    request_id: str,
    *,
    status: Sam3VideoTrackCancelStatus,
    warning_code: str | None = None,
    warning_message: str | None = None,
    action: str | None = None,
    diagnostics: Mapping[str, object],
) -> Sam3VideoTrackCancelResult:
    warning = (
        Sam3Warning(
            code=warning_code,
            message=warning_message or "",
            severity="error" if status == "failed" else "warning",
            action=action,
        )
        if warning_code
        else None
    )
    return Sam3VideoTrackCancelResult(
        request_id=request_id,
        status=status,
        warnings=(warning,) if warning is not None else (),
        recommended_actions=(action,) if action else (),
        diagnostics=diagnostics,
    )


def _diagnostics(
    status: Sam3VideoTrackCancelStatus,
    extra: Mapping[str, object] | None,
    *,
    failure_code: str | None = None,
    exception_type: str | None = None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_CANCEL_RESULT_SCHEMA_VERSION,
        "source": _CANCEL_SOURCE,
        "status": status,
    }
    if failure_code:
        diagnostics["failureCode"] = failure_code
    if exception_type:
        diagnostics["exceptionType"] = exception_type
    if extra:
        diagnostics["extra"] = dict(extra)
    return _scrubbed_mapping(diagnostics)


def _raw_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


def _dict_items(value: object) -> tuple[dict[str, Any], ...]:
    return tuple(item for item in _list_items(value) if isinstance(item, dict))


def _list_items(value: object) -> tuple[Any, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return ()


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
    "SAM3_VIDEO_TRACK_CANCEL_RESULT_SCHEMA_VERSION",
    "Sam3VideoTrackCancelError",
    "Sam3VideoTrackCancelResult",
    "Sam3VideoTrackCancelStatus",
    "cancel_video_track_job",
]
