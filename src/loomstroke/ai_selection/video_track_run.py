"""Pure run result helpers for SAM3 Tier 6 video tracking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .data_model import BlockedReason, Sam3Warning
from .metadata_scrub import scrub_sam3_metadata
from .video_track_request import Sam3VideoTrackRequest
from .video_tracker_model import Sam3VideoTrackResult

SAM3_VIDEO_TRACK_RUN_RESULT_SCHEMA_VERSION = "sam3.video_track_run_result.v1"

Sam3VideoTrackRunStatus = Literal["completed", "cancelled", "blocked", "failed"]
_RUN_STATUS_VALUES: tuple[Sam3VideoTrackRunStatus, ...] = (
    "completed",
    "cancelled",
    "blocked",
    "failed",
)
_REDACTED = "<redacted>"


class Sam3VideoTrackRunError(ValueError):
    """Raised when a SAM3 video track run result cannot be represented safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackRunResult:
    request: Sam3VideoTrackRequest
    status: Sam3VideoTrackRunStatus
    track_result: Sam3VideoTrackResult | None = None
    blocked_reasons: tuple[BlockedReason, ...] = field(default_factory=tuple)
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)
    recommended_actions: tuple[str, ...] = field(default_factory=tuple)
    partial_frame_count: int = 0
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_RUN_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        request = _track_request(self.request)
        status = _normalize_status(self.status)
        track_result = _optional_track_result(self.track_result)
        if track_result is not None:
            _validate_source_match(request, track_result)

        source_frame_count = len(request.source_manifest.frames)
        partial_frame_count = _partial_frame_count(self.partial_frame_count, track_result)
        if status == "completed":
            if track_result is None:
                raise Sam3VideoTrackRunError(
                    "video_track_run_completed_result_missing",
                    "Completed SAM3 video track runs require a track result.",
                )
            if len(track_result.frames) < source_frame_count:
                raise Sam3VideoTrackRunError(
                    "video_track_run_completed_frames_incomplete",
                    "Completed SAM3 video track runs require all source frames.",
                )
            partial_frame_count = len(track_result.frames)

        object.__setattr__(self, "request", request)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "track_result", track_result)
        object.__setattr__(self, "blocked_reasons", tuple(self.blocked_reasons))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(
            self,
            "recommended_actions",
            _recommended_actions(self.recommended_actions),
        )
        object.__setattr__(self, "partial_frame_count", partial_frame_count)
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def partial(self) -> bool:
        return not self.completed and self.partial_frame_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "blocked": self.blocked,
            "failed": self.failed,
            "partial": self.partial,
            "request": self.request.to_dict(),
            "requestId": self.request.request_id,
            "sourceManifestId": self.request.source_manifest.manifest_id,
            "trackResult": (self.track_result.to_dict() if self.track_result is not None else None),
            "frameCount": len(self.request.source_manifest.frames),
            "partialFrameCount": self.partial_frame_count,
            "blockedReasons": [reason.to_dict() for reason in self.blocked_reasons],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "recommendedActions": list(self.recommended_actions),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackRunResult:
        return cls(
            request=Sam3VideoTrackRequest.from_dict(_dict(value.get("request"))),
            status=_normalize_status(value.get("status")),
            track_result=_track_result_from_payload(value.get("trackResult")),
            blocked_reasons=tuple(
                BlockedReason.from_dict(item) for item in _dict_items(value.get("blockedReasons"))
            ),
            warnings=tuple(
                Sam3Warning.from_dict(item) for item in _dict_items(value.get("warnings"))
            ),
            recommended_actions=tuple(
                str(item) for item in _list_items(value.get("recommendedActions"))
            ),
            partial_frame_count=_non_negative_int(value.get("partialFrameCount", 0)),
            diagnostics=_dict(value.get("diagnostics")),
            schema_version=str(
                value.get("schemaVersion", SAM3_VIDEO_TRACK_RUN_RESULT_SCHEMA_VERSION)
            ),
        )


def build_video_track_run_result(
    *,
    request: Sam3VideoTrackRequest,
    status: Sam3VideoTrackRunStatus,
    track_result: Sam3VideoTrackResult | None = None,
    blocked_reasons: Sequence[BlockedReason] = (),
    warnings: Sequence[Sam3Warning] = (),
    recommended_actions: Sequence[str] = (),
    partial_frame_count: int | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoTrackRunResult:
    """Build a file-write-free run result for one SAM3 video tracking request."""

    return Sam3VideoTrackRunResult(
        request=request,
        status=status,
        track_result=track_result,
        blocked_reasons=tuple(blocked_reasons),
        warnings=tuple(warnings),
        recommended_actions=tuple(recommended_actions),
        partial_frame_count=_run_partial_frame_count(
            partial_frame_count,
            track_result,
        ),
        diagnostics=diagnostics or {},
    )


def _track_request(value: object) -> Sam3VideoTrackRequest:
    if not isinstance(value, Sam3VideoTrackRequest):
        raise Sam3VideoTrackRunError(
            "video_track_run_request_invalid",
            "SAM3 video track run result requires a video track request.",
        )
    return value


def _optional_track_result(value: object) -> Sam3VideoTrackResult | None:
    if value is None:
        return None
    if not isinstance(value, Sam3VideoTrackResult):
        raise Sam3VideoTrackRunError(
            "video_track_run_result_invalid",
            "SAM3 video track run result requires a video track result.",
        )
    return value


def _track_result_from_payload(value: object) -> Sam3VideoTrackResult | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    return Sam3VideoTrackResult.from_dict(dict(value))


def _validate_source_match(
    request: Sam3VideoTrackRequest,
    track_result: Sam3VideoTrackResult,
) -> None:
    if track_result.source_video_asset_id != request.source_manifest.source_asset_id:
        raise Sam3VideoTrackRunError(
            "video_track_run_source_mismatch",
            "SAM3 video track result source must match the request source.",
        )


def _normalize_status(value: object) -> Sam3VideoTrackRunStatus:
    text = str(value or "").strip()
    if text not in _RUN_STATUS_VALUES:
        raise Sam3VideoTrackRunError(
            "video_track_run_status_invalid",
            "SAM3 video track run status is not supported.",
        )
    return cast(Sam3VideoTrackRunStatus, text)


def _partial_frame_count(
    value: object,
    track_result: Sam3VideoTrackResult | None,
) -> int:
    normalized = _non_negative_int(value)
    if normalized == 0 and track_result is not None:
        return len(track_result.frames)
    return normalized


def _run_partial_frame_count(
    value: int | None,
    track_result: Sam3VideoTrackResult | None,
) -> int:
    if value is None and track_result is not None:
        return len(track_result.frames)
    return value or 0


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _recommended_actions(values: object) -> tuple[str, ...]:
    return tuple(str(value) for value in _list_items(values) if str(value).strip())


def _dict(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return _scrubbed_mapping(value)


def _dict_items(value: object) -> tuple[dict[str, Any], ...]:
    return tuple(item for item in _list_items(value) if isinstance(item, dict))


def _list_items(value: object) -> tuple[Any, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return ()


def _scrubbed_mapping(value: Mapping[str, object]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
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
    "SAM3_VIDEO_TRACK_RUN_RESULT_SCHEMA_VERSION",
    "Sam3VideoTrackRunError",
    "Sam3VideoTrackRunResult",
    "Sam3VideoTrackRunStatus",
    "build_video_track_run_result",
]
