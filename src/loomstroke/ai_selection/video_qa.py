"""Deterministic QA helpers for SAM3 video tracking results."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from .metadata_scrub import scrub_sam3_metadata
from .video_tracker_model import Sam3VideoMaskFrame, Sam3VideoTrackResult

SAM3_VIDEO_TRACK_QA_ISSUE_SCHEMA_VERSION = "sam3.video_track_qa_issue.v1"
SAM3_VIDEO_TRACK_QA_OBJECT_COVERAGE_SCHEMA_VERSION = "sam3.video_track_qa_object_coverage.v1"
SAM3_VIDEO_TRACK_QA_SCORECARD_SCHEMA_VERSION = "sam3.video_track_qa_scorecard.v1"

WarningSeverity = Literal["info", "warning", "error"]

_ISSUE_CODE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "_.:-"
)
_VALID_SEVERITIES = frozenset(("info", "warning", "error"))
_ACTION_BY_CODE = {
    "duplicate_frame_index": "dedupe_frames",
    "duplicate_track_object": "review_object_id_timeline",
    "empty_frame_masks": "rerun_tracker_or_review_frame",
    "frame_warning": "review_frame_warning",
    "missing_object": "review_tracking_prompt",
    "non_monotonic_frame_order": "review_frame_sequence",
    "object_reappeared": "review_reappeared_object",
    "unexpected_object_mask": "review_object_id_timeline",
}


class Sam3VideoTrackQAError(ValueError):
    """Raised when a SAM3 video track result cannot be analyzed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackQAIssue:
    """One deterministic issue found in a SAM3 video track result."""

    code: str
    severity: WarningSeverity
    message: str
    frame_index: int | None = None
    object_id: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_QA_ISSUE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _safe_issue_code(self.code) or "unknown")
        severity = str(self.severity or "warning")
        if severity not in _VALID_SEVERITIES:
            severity = "warning"
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "message", str(self.message or ""))
        object.__setattr__(self, "frame_index", _optional_non_negative_int(self.frame_index))
        object.__setattr__(self, "object_id", _optional_id(self.object_id))
        object.__setattr__(self, "details", _scrubbed_mapping(self.details))

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "frameIndex": self.frame_index,
            "objectId": self.object_id,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackQAObjectCoverage:
    """Frame coverage summary for one expected SAM3 video object."""

    object_id: str
    expected: bool
    declared_first_frame_index: int
    expected_frame_count: int
    present_frame_count: int
    missing_frame_count: int
    first_seen_frame_index: int | None
    last_seen_frame_index: int | None
    schema_version: str = SAM3_VIDEO_TRACK_QA_OBJECT_COVERAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_id", _optional_id(self.object_id) or "unknown")
        object.__setattr__(
            self,
            "declared_first_frame_index",
            _non_negative_int(self.declared_first_frame_index),
        )
        object.__setattr__(
            self,
            "expected_frame_count",
            _non_negative_int(self.expected_frame_count),
        )
        object.__setattr__(
            self,
            "present_frame_count",
            _non_negative_int(self.present_frame_count),
        )
        object.__setattr__(
            self,
            "missing_frame_count",
            _non_negative_int(self.missing_frame_count),
        )
        object.__setattr__(
            self,
            "first_seen_frame_index",
            _optional_non_negative_int(self.first_seen_frame_index),
        )
        object.__setattr__(
            self,
            "last_seen_frame_index",
            _optional_non_negative_int(self.last_seen_frame_index),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "objectId": self.object_id,
            "expected": self.expected,
            "declaredFirstFrameIndex": self.declared_first_frame_index,
            "expectedFrameCount": self.expected_frame_count,
            "presentFrameCount": self.present_frame_count,
            "missingFrameCount": self.missing_frame_count,
            "firstSeenFrameIndex": self.first_seen_frame_index,
            "lastSeenFrameIndex": self.last_seen_frame_index,
        }


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackQAScorecard:
    """Deterministic QA summary for a SAM3 video track result."""

    track_id: str
    status: Literal["passed", "warning", "failed"]
    object_count: int
    frame_count: int
    issue_count: int
    warning_count: int
    error_count: int
    info_count: int
    issues: tuple[Sam3VideoTrackQAIssue, ...]
    frame_warnings: tuple[dict[str, object], ...]
    object_coverage: tuple[Sam3VideoTrackQAObjectCoverage, ...]
    recommended_actions: tuple[str, ...]
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_QA_SCORECARD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_id", str(self.track_id or ""))
        object.__setattr__(self, "object_count", _non_negative_int(self.object_count))
        object.__setattr__(self, "frame_count", _non_negative_int(self.frame_count))
        object.__setattr__(self, "issue_count", _non_negative_int(self.issue_count))
        object.__setattr__(self, "warning_count", _non_negative_int(self.warning_count))
        object.__setattr__(self, "error_count", _non_negative_int(self.error_count))
        object.__setattr__(self, "info_count", _non_negative_int(self.info_count))
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    def to_dict(self) -> dict[str, object]:
        payload = {
            "schemaVersion": self.schema_version,
            "trackId": self.track_id,
            "status": self.status,
            "ok": self.status == "passed",
            "objectCount": self.object_count,
            "frameCount": self.frame_count,
            "issueCount": self.issue_count,
            "warningCount": self.warning_count,
            "errorCount": self.error_count,
            "infoCount": self.info_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "frameWarnings": [dict(entry) for entry in self.frame_warnings],
            "objectCoverage": [coverage.to_dict() for coverage in self.object_coverage],
            "recommendedActions": list(self.recommended_actions),
            "diagnostics": dict(self.diagnostics),
        }
        return dict(scrub_sam3_metadata(payload))


def analyze_video_track_result(
    result: Sam3VideoTrackResult,
    *,
    expected_object_ids: Iterable[str] = (),
) -> Sam3VideoTrackQAScorecard:
    """Analyze a SAM3 video track result using metadata already in the result."""

    if not isinstance(result, Sam3VideoTrackResult):
        raise Sam3VideoTrackQAError(
            "video_track_qa_result_invalid",
            "SAM3 video track QA requires a video track result.",
        )

    expected_first_frames = _expected_first_frames(result, expected_object_ids)
    issues = [
        *_duplicate_object_issues(result),
        *_frame_order_issues(result),
    ]

    sorted_frames = tuple(
        frame
        for _, frame in sorted(
            enumerate(result.frames),
            key=lambda item: (item[1].frame_index, item[0]),
        )
    )
    frame_issues, frame_warnings, object_coverage = _analyze_frame_timeline(
        sorted_frames,
        expected_first_frames,
    )
    issues.extend(frame_issues)

    issues_tuple = tuple(issues)
    error_count = sum(1 for issue in issues_tuple if issue.severity == "error")
    warning_count = sum(1 for issue in issues_tuple if issue.severity == "warning")
    info_count = sum(1 for issue in issues_tuple if issue.severity == "info")
    status: Literal["passed", "warning", "failed"]
    if error_count:
        status = "failed"
    elif warning_count:
        status = "warning"
    else:
        status = "passed"

    return Sam3VideoTrackQAScorecard(
        track_id=result.track_id,
        status=status,
        object_count=len(result.objects),
        frame_count=len(result.frames),
        issue_count=len(issues_tuple),
        warning_count=warning_count,
        error_count=error_count,
        info_count=info_count,
        issues=issues_tuple,
        frame_warnings=tuple(frame_warnings),
        object_coverage=object_coverage,
        recommended_actions=_recommended_actions(issues_tuple),
        diagnostics=result.diagnostics,
    )


def _analyze_frame_timeline(
    frames: tuple[Sam3VideoMaskFrame, ...],
    expected_first_frames: Mapping[str, int],
) -> tuple[
    list[Sam3VideoTrackQAIssue],
    list[dict[str, object]],
    tuple[Sam3VideoTrackQAObjectCoverage, ...],
]:
    issues: list[Sam3VideoTrackQAIssue] = []
    frame_warnings: list[dict[str, object]] = []
    coverage_state = {
        object_id: _ObjectCoverageState(object_id, first_frame)
        for object_id, first_frame in expected_first_frames.items()
    }
    missing_streaks = {object_id: 0 for object_id in expected_first_frames}
    seen_once = {object_id: False for object_id in expected_first_frames}

    for frame in frames:
        frame_object_ids = set(frame.object_masks)
        _append_frame_payload_issues(
            issues,
            frame_warnings,
            frame,
            expected_first_frames,
            frame_object_ids,
        )
        _append_expected_object_issues(
            issues,
            frame,
            expected_first_frames,
            frame_object_ids,
            coverage_state,
            missing_streaks,
            seen_once,
        )
    return (
        issues,
        frame_warnings,
        tuple(coverage.to_model() for coverage in coverage_state.values()),
    )


def _append_frame_payload_issues(
    issues: list[Sam3VideoTrackQAIssue],
    frame_warnings: list[dict[str, object]],
    frame: Sam3VideoMaskFrame,
    expected_first_frames: Mapping[str, int],
    frame_object_ids: set[str],
) -> None:
    if not frame.object_masks:
        issues.append(
            _issue(
                "empty_frame_masks",
                "warning",
                "SAM3 video track frame has no object masks.",
                frame_index=frame.frame_index,
            )
        )
    if frame.warnings:
        warnings = list(frame.warnings)
        frame_warnings.append(
            {
                "frameIndex": frame.frame_index,
                "timestampMs": frame.timestamp_ms,
                "warnings": warnings,
            }
        )
        for warning_code in warnings:
            issues.append(
                _issue(
                    "frame_warning",
                    "warning",
                    "SAM3 video track frame reported a warning.",
                    frame_index=frame.frame_index,
                    details={"warningCode": warning_code},
                )
            )

    for object_id in sorted(frame_object_ids):
        if object_id in expected_first_frames:
            continue
        issues.append(
            _issue(
                "unexpected_object_mask",
                "warning",
                "SAM3 video track frame contains an unexpected object mask.",
                frame_index=frame.frame_index,
                object_id=object_id,
            )
        )


def _append_expected_object_issues(
    issues: list[Sam3VideoTrackQAIssue],
    frame: Sam3VideoMaskFrame,
    expected_first_frames: Mapping[str, int],
    frame_object_ids: set[str],
    coverage_state: Mapping[str, _ObjectCoverageState],
    missing_streaks: dict[str, int],
    seen_once: dict[str, bool],
) -> None:
    for object_id, first_frame_index in expected_first_frames.items():
        if frame.frame_index < first_frame_index:
            continue
        coverage = coverage_state[object_id]
        coverage.expected_frame_count += 1
        if object_id in frame_object_ids:
            _mark_object_present(
                issues,
                frame,
                object_id,
                coverage,
                missing_streaks,
                seen_once,
            )
            continue
        coverage.missing_frame_count += 1
        missing_streaks[object_id] += 1
        issues.append(
            _issue(
                "missing_object",
                "warning",
                "SAM3 video track object is missing from an expected frame.",
                frame_index=frame.frame_index,
                object_id=object_id,
                details={"declaredFirstFrameIndex": first_frame_index},
            )
        )


def _mark_object_present(
    issues: list[Sam3VideoTrackQAIssue],
    frame: Sam3VideoMaskFrame,
    object_id: str,
    coverage: _ObjectCoverageState,
    missing_streaks: dict[str, int],
    seen_once: dict[str, bool],
) -> None:
    coverage.present_frame_count += 1
    coverage.first_seen_frame_index = (
        frame.frame_index
        if coverage.first_seen_frame_index is None
        else min(coverage.first_seen_frame_index, frame.frame_index)
    )
    coverage.last_seen_frame_index = (
        frame.frame_index
        if coverage.last_seen_frame_index is None
        else max(coverage.last_seen_frame_index, frame.frame_index)
    )
    if missing_streaks[object_id] > 0 and seen_once[object_id]:
        issues.append(
            _issue(
                "object_reappeared",
                "info",
                "SAM3 video track object reappeared after missing frames.",
                frame_index=frame.frame_index,
                object_id=object_id,
                details={"missingFrameCount": missing_streaks[object_id]},
            )
        )
    missing_streaks[object_id] = 0
    seen_once[object_id] = True


@dataclass(slots=True)
class _ObjectCoverageState:
    object_id: str
    declared_first_frame_index: int
    expected_frame_count: int = 0
    present_frame_count: int = 0
    missing_frame_count: int = 0
    first_seen_frame_index: int | None = None
    last_seen_frame_index: int | None = None

    def to_model(self) -> Sam3VideoTrackQAObjectCoverage:
        return Sam3VideoTrackQAObjectCoverage(
            object_id=self.object_id,
            expected=True,
            declared_first_frame_index=self.declared_first_frame_index,
            expected_frame_count=self.expected_frame_count,
            present_frame_count=self.present_frame_count,
            missing_frame_count=self.missing_frame_count,
            first_seen_frame_index=self.first_seen_frame_index,
            last_seen_frame_index=self.last_seen_frame_index,
        )


def _expected_first_frames(
    result: Sam3VideoTrackResult,
    expected_object_ids: Iterable[str],
) -> dict[str, int]:
    first_frames: dict[str, int] = {}
    for item in result.objects:
        object_id = _optional_id(item.object_id)
        if object_id is None or object_id in first_frames:
            continue
        first_frames[object_id] = _non_negative_int(item.first_frame_index)
    for raw_object_id in expected_object_ids:
        object_id = _optional_id(raw_object_id)
        if object_id is None or object_id in first_frames:
            continue
        first_frames[object_id] = 0
    return dict(sorted(first_frames.items()))


def _duplicate_object_issues(result: Sam3VideoTrackResult) -> tuple[Sam3VideoTrackQAIssue, ...]:
    counts = Counter(item.object_id for item in result.objects)
    return tuple(
        _issue(
            "duplicate_track_object",
            "warning",
            "SAM3 video track result declares the same object more than once.",
            object_id=object_id,
            details={"occurrenceCount": count},
        )
        for object_id, count in sorted(counts.items())
        if count > 1
    )


def _frame_order_issues(result: Sam3VideoTrackResult) -> tuple[Sam3VideoTrackQAIssue, ...]:
    issues: list[Sam3VideoTrackQAIssue] = []
    previous_index: int | None = None
    for frame in result.frames:
        if previous_index is not None and frame.frame_index < previous_index:
            issues.append(
                _issue(
                    "non_monotonic_frame_order",
                    "warning",
                    "SAM3 video track frames are not ordered by frame index.",
                    frame_index=frame.frame_index,
                    details={"previousFrameIndex": previous_index},
                )
            )
        previous_index = frame.frame_index

    counts = Counter(frame.frame_index for frame in result.frames)
    for frame_index, count in sorted(counts.items()):
        if count <= 1:
            continue
        issues.append(
            _issue(
                "duplicate_frame_index",
                "error",
                "SAM3 video track result contains duplicate frame indices.",
                frame_index=frame_index,
                details={"occurrenceCount": count},
            )
        )
    return tuple(issues)


def _issue(
    code: str,
    severity: WarningSeverity,
    message: str,
    *,
    frame_index: int | None = None,
    object_id: str | None = None,
    details: Mapping[str, object] | None = None,
) -> Sam3VideoTrackQAIssue:
    return Sam3VideoTrackQAIssue(
        code=code,
        severity=severity,
        message=message,
        frame_index=frame_index,
        object_id=object_id,
        details=details or {},
    )


def _recommended_actions(issues: tuple[Sam3VideoTrackQAIssue, ...]) -> tuple[str, ...]:
    actions: list[str] = []
    for issue in issues:
        action = _ACTION_BY_CODE.get(issue.code)
        if action is not None and action not in actions:
            actions.append(action)
    return tuple(actions)


def _scrubbed_mapping(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    scrubbed = scrub_sam3_metadata(value)
    return dict(scrubbed) if isinstance(scrubbed, Mapping) else {}


def _safe_issue_code(value: object) -> str:
    text = "_".join(str(value or "").strip().split())
    return "".join(char for char in text if char in _ISSUE_CODE_CHARS)[:80]


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    return _non_negative_int(value)


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "SAM3_VIDEO_TRACK_QA_ISSUE_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_QA_OBJECT_COVERAGE_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_QA_SCORECARD_SCHEMA_VERSION",
    "Sam3VideoTrackQAError",
    "Sam3VideoTrackQAIssue",
    "Sam3VideoTrackQAObjectCoverage",
    "Sam3VideoTrackQAScorecard",
    "WarningSeverity",
    "analyze_video_track_result",
]
