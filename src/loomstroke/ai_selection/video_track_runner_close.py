"""Runner close result helpers for SAM3 video tracking."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .data_model import Sam3Warning
from .metadata_scrub import scrub_sam3_metadata
from .video_track_runner import Sam3VideoTrackRunner

SAM3_VIDEO_TRACK_RUNNER_CLOSE_RESULT_SCHEMA_VERSION = "sam3.video_track_runner_close_result.v1"

Sam3VideoTrackRunnerCloseStatus = Literal["closed", "failed"]
_CLOSE_STATUSES: tuple[Sam3VideoTrackRunnerCloseStatus, ...] = ("closed", "failed")
_CLOSE_SOURCE = "sam3_video_track_runner_close"
_DEFAULT_REASON = "idle_unload"
_RETRY_ACTION = "retry_video_track_runner_close"
_REDACTED = "<redacted>"


class Sam3VideoTrackRunnerCloseError(ValueError):
    """Raised when a SAM3 video track runner close result cannot be represented."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackRunnerCloseResult:
    status: Sam3VideoTrackRunnerCloseStatus
    reason: str = _DEFAULT_REASON
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)
    recommended_actions: tuple[str, ...] = field(default_factory=tuple)
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_RUNNER_CLOSE_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _close_status(self.status))
        object.__setattr__(self, "reason", _reason(self.reason))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(
            self,
            "recommended_actions",
            tuple(str(action) for action in self.recommended_actions if str(action).strip()),
        )
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    @property
    def closed(self) -> bool:
        return self.status == "closed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "closed": self.closed,
            "reason": self.reason,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "recommendedActions": list(self.recommended_actions),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackRunnerCloseResult:
        return cls(
            status=_close_status(value.get("status", "failed")),
            reason=str(value.get("reason", _DEFAULT_REASON)),
            warnings=tuple(
                Sam3Warning.from_dict(item) for item in _dict_items(value.get("warnings"))
            ),
            recommended_actions=tuple(
                str(item) for item in _list_items(value.get("recommendedActions"))
            ),
            diagnostics=_scrubbed_mapping(_raw_dict(value.get("diagnostics"))),
            schema_version=str(
                value.get(
                    "schemaVersion",
                    SAM3_VIDEO_TRACK_RUNNER_CLOSE_RESULT_SCHEMA_VERSION,
                )
            ),
        )


async def close_video_track_runner(
    runner: Sam3VideoTrackRunner,
    *,
    reason: str = _DEFAULT_REASON,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoTrackRunnerCloseResult:
    """Close a SAM3 video track runner and return a secret-free result."""

    track_runner = _runner(runner)
    normalized_reason = _reason(reason)
    try:
        await track_runner.close()
    except Exception as exc:
        return _result(
            status="failed",
            reason=normalized_reason,
            warning_code="video_track_runner_close_exception",
            warning_message="SAM3 video track runner close raised an exception.",
            action=_RETRY_ACTION,
            diagnostics=_diagnostics(
                "failed",
                normalized_reason,
                diagnostics,
                failure_code="video_track_runner_close_exception",
                exception_type=type(exc).__name__,
            ),
        )

    return _result(
        status="closed",
        reason=normalized_reason,
        diagnostics=_diagnostics("closed", normalized_reason, diagnostics),
    )


def _runner(value: object) -> Sam3VideoTrackRunner:
    if not callable(getattr(value, "close", None)):
        raise Sam3VideoTrackRunnerCloseError(
            "video_track_runner_close_runner_invalid",
            "SAM3 video track runner close requires a runner with a close method.",
        )
    return cast(Sam3VideoTrackRunner, value)


def _reason(value: object) -> str:
    text = str(value or "").strip()
    return text or _DEFAULT_REASON


def _close_status(value: object) -> Sam3VideoTrackRunnerCloseStatus:
    text = str(value or "").strip()
    if text not in _CLOSE_STATUSES:
        raise Sam3VideoTrackRunnerCloseError(
            "video_track_runner_close_status_invalid",
            "SAM3 video track runner close status is not supported.",
        )
    return cast(Sam3VideoTrackRunnerCloseStatus, text)


def _result(
    *,
    status: Sam3VideoTrackRunnerCloseStatus,
    reason: str,
    warning_code: str | None = None,
    warning_message: str | None = None,
    action: str | None = None,
    diagnostics: Mapping[str, object],
) -> Sam3VideoTrackRunnerCloseResult:
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
    return Sam3VideoTrackRunnerCloseResult(
        status=status,
        reason=reason,
        warnings=(warning,) if warning is not None else (),
        recommended_actions=(action,) if action else (),
        diagnostics=diagnostics,
    )


def _diagnostics(
    status: Sam3VideoTrackRunnerCloseStatus,
    reason: str,
    extra: Mapping[str, object] | None,
    *,
    failure_code: str | None = None,
    exception_type: str | None = None,
) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_RUNNER_CLOSE_RESULT_SCHEMA_VERSION,
        "source": _CLOSE_SOURCE,
        "status": status,
        "reason": reason,
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
    "SAM3_VIDEO_TRACK_RUNNER_CLOSE_RESULT_SCHEMA_VERSION",
    "Sam3VideoTrackRunnerCloseError",
    "Sam3VideoTrackRunnerCloseResult",
    "Sam3VideoTrackRunnerCloseStatus",
    "close_video_track_runner",
]
