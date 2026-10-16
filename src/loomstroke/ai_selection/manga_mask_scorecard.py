"""Metadata-only scorecards for SAM3 manga mask candidates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from .manga_mask_package import (
    Sam3ComicMaskCandidateRef,
    Sam3ComicMaskPackage,
)

SAM3_MANGA_MASK_CANDIDATE_SCORECARD_SCHEMA_VERSION = "sam3.manga_mask_candidate_scorecard.v1"

WarningSeverity = Literal["info", "warning", "error"]
CandidateScope = Literal["panel", "global"]


class Sam3MangaMaskScorecardError(ValueError):
    """Raised when a SAM3 manga mask scorecard cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskCandidateScoreWarning:
    """One deterministic scorecard warning for a manga mask candidate."""

    code: str
    message: str
    severity: WarningSeverity = "warning"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskCandidateScore:
    """Metadata-only quality summary for one manga mask candidate."""

    candidate_id: str
    scope: CandidateScope
    category: str
    accepted: bool
    quality_score: float
    confidence_score: float | None
    area_ratio: float | None
    bbox_within_source: bool | None
    ready: bool
    warnings: tuple[Sam3MangaMaskCandidateScoreWarning, ...]
    recommended_actions: tuple[str, ...]
    panel_id: str | None = None
    schema_version: str = SAM3_MANGA_MASK_CANDIDATE_SCORECARD_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "candidateId": self.candidate_id,
            "scope": self.scope,
            "panelId": self.panel_id,
            "category": self.category,
            "accepted": self.accepted,
            "qualityScore": self.quality_score,
            "confidenceScore": self.confidence_score,
            "areaRatio": self.area_ratio,
            "bboxWithinSource": self.bbox_within_source,
            "ready": self.ready,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "recommendedActions": list(self.recommended_actions),
        }


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskPackageScorecard:
    """Metadata-only scorecard for a SAM3 comic mask package."""

    package_id: str
    source_page_id: str
    candidate_count: int
    accepted_candidate_count: int
    ready_candidate_count: int
    ok: bool
    candidates: tuple[Sam3MangaMaskCandidateScore, ...]
    recommended_actions: tuple[str, ...]
    schema_version: str = SAM3_MANGA_MASK_CANDIDATE_SCORECARD_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "packageId": self.package_id,
            "sourcePageId": self.source_page_id,
            "candidateCount": self.candidate_count,
            "acceptedCandidateCount": self.accepted_candidate_count,
            "readyCandidateCount": self.ready_candidate_count,
            "ok": self.ok,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "recommendedActions": list(self.recommended_actions),
        }


def score_comic_mask_package_candidates(
    package: Sam3ComicMaskPackage,
    *,
    canvas_width: object | None = None,
    canvas_height: object | None = None,
    min_confidence: object = 0.35,
    min_area_ratio: object = 0.001,
    max_area_ratio: object = 0.95,
) -> Sam3MangaMaskPackageScorecard:
    """Build a deterministic metadata-only scorecard for comic mask candidates."""

    if not isinstance(package, Sam3ComicMaskPackage):
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_package_invalid",
            "SAM3 manga mask scorecards require a comic mask package.",
        )

    canvas_size = _optional_canvas_size(canvas_width, canvas_height)
    thresholds = _thresholds(
        min_confidence=min_confidence,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
    )
    candidate_scores = tuple(
        _score_candidate(
            candidate,
            scope=scope,
            panel_id=panel_id,
            canvas_size=canvas_size,
            min_confidence=thresholds[0],
            min_area_ratio=thresholds[1],
            max_area_ratio=thresholds[2],
        )
        for scope, panel_id, candidate in _candidate_items(package)
    )
    accepted = tuple(score for score in candidate_scores if score.accepted)
    ready = tuple(score for score in candidate_scores if score.ready)
    actions = _package_recommended_actions(candidate_scores, accepted)
    ok = bool(accepted) and all(score.ready for score in accepted)
    return Sam3MangaMaskPackageScorecard(
        package_id=package.package_id,
        source_page_id=package.source_page_id,
        candidate_count=len(candidate_scores),
        accepted_candidate_count=len(accepted),
        ready_candidate_count=len(ready),
        ok=ok,
        candidates=candidate_scores,
        recommended_actions=actions,
    )


def _score_candidate(
    candidate: Sam3ComicMaskCandidateRef,
    *,
    scope: CandidateScope,
    panel_id: str | None,
    canvas_size: tuple[int, int] | None,
    min_confidence: float,
    min_area_ratio: float,
    max_area_ratio: float,
) -> Sam3MangaMaskCandidateScore:
    confidence = _confidence_score(candidate.score)
    quality_score = confidence if confidence is not None else 0.0
    area_ratio = _area_ratio(candidate.area_px, canvas_size)
    bbox_within_source = _bbox_within_source(candidate.bbox, canvas_size)

    warnings: list[Sam3MangaMaskCandidateScoreWarning] = []
    actions: list[str] = []
    if confidence is None:
        _append_warning(
            warnings,
            actions,
            code="candidate_score_missing",
            message="SAM3 manga mask candidate does not have a confidence score.",
            action="review_candidate",
        )
    elif confidence < min_confidence:
        _append_warning(
            warnings,
            actions,
            code="candidate_confidence_low",
            message="SAM3 manga mask candidate confidence is below the configured threshold.",
            action="review_or_refine_candidate",
        )

    _, _, bbox_width, bbox_height = candidate.bbox
    if candidate.area_px <= 0 or bbox_width <= 0 or bbox_height <= 0:
        _append_warning(
            warnings,
            actions,
            code="candidate_area_empty",
            message="SAM3 manga mask candidate area is empty.",
            severity="error",
            action="reselect_candidate",
        )
    if area_ratio is not None and 0.0 <= area_ratio < min_area_ratio:
        _append_warning(
            warnings,
            actions,
            code="candidate_area_too_small",
            message="SAM3 manga mask candidate covers a very small source area.",
            action="refine_expand",
        )
    if area_ratio is not None and area_ratio > max_area_ratio:
        _append_warning(
            warnings,
            actions,
            code="candidate_area_too_large",
            message="SAM3 manga mask candidate covers too much of the source area.",
            action="refine_contract_or_reselect",
        )
    if bbox_within_source is False:
        _append_warning(
            warnings,
            actions,
            code="candidate_bbox_outside_source",
            message="SAM3 manga mask candidate bbox is outside the source bounds.",
            severity="error",
            action="review_panel_bounds",
        )
    if not candidate.accepted:
        _append_warning(
            warnings,
            actions,
            code="candidate_not_accepted",
            message="SAM3 manga mask candidate has not been accepted by the user.",
            severity="info",
            action="accept_candidate",
        )

    ready = candidate.accepted and not any(warning.severity == "error" for warning in warnings)
    return Sam3MangaMaskCandidateScore(
        candidate_id=candidate.candidate_id,
        scope=scope,
        panel_id=panel_id,
        category=candidate.category,
        accepted=candidate.accepted,
        quality_score=quality_score,
        confidence_score=confidence,
        area_ratio=area_ratio,
        bbox_within_source=bbox_within_source,
        ready=ready,
        warnings=tuple(warnings),
        recommended_actions=tuple(actions),
    )


def _candidate_items(
    package: Sam3ComicMaskPackage,
) -> tuple[tuple[CandidateScope, str | None, Sam3ComicMaskCandidateRef], ...]:
    items: list[tuple[CandidateScope, str | None, Sam3ComicMaskCandidateRef]] = []
    for panel in package.panel_masks:
        items.extend(("panel", panel.panel_id, candidate) for candidate in panel.candidates)
    items.extend(("global", candidate.panel_id, candidate) for candidate in package.global_masks)
    return tuple(items)


def _package_recommended_actions(
    candidate_scores: tuple[Sam3MangaMaskCandidateScore, ...],
    accepted_scores: tuple[Sam3MangaMaskCandidateScore, ...],
) -> tuple[str, ...]:
    actions: list[str] = []
    for score in candidate_scores:
        for action in score.recommended_actions:
            if action not in actions:
                actions.append(action)
    if not accepted_scores and "accept_candidate" not in actions:
        actions.append("accept_candidate")
    return tuple(actions)


def _append_warning(
    warnings: list[Sam3MangaMaskCandidateScoreWarning],
    actions: list[str],
    *,
    code: str,
    message: str,
    action: str,
    severity: WarningSeverity = "warning",
) -> None:
    warnings.append(
        Sam3MangaMaskCandidateScoreWarning(
            code=code,
            message=message,
            severity=severity,
        )
    )
    if action not in actions:
        actions.append(action)


def _confidence_score(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return max(0.0, min(1.0, numeric))


def _area_ratio(area_px: int, canvas_size: tuple[int, int] | None) -> float | None:
    if canvas_size is None:
        return None
    width, height = canvas_size
    return max(0.0, float(area_px)) / float(width * height)


def _bbox_within_source(
    bbox: tuple[int, int, int, int],
    canvas_size: tuple[int, int] | None,
) -> bool | None:
    if canvas_size is None:
        return None
    canvas_width, canvas_height = canvas_size
    x, y, width, height = bbox
    return (
        x >= 0
        and y >= 0
        and width >= 0
        and height >= 0
        and (x + width <= canvas_width and y + height <= canvas_height)
    )


def _optional_canvas_size(
    canvas_width: object | None,
    canvas_height: object | None,
) -> tuple[int, int] | None:
    if canvas_width is None and canvas_height is None:
        return None
    if canvas_width is None or canvas_height is None:
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_canvas_size_invalid",
            "SAM3 manga mask scorecards require both canvas width and height.",
        )
    width = _positive_int(canvas_width, "canvas_width")
    height = _positive_int(canvas_height, "canvas_height")
    return (width, height)


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_canvas_size_invalid",
            f"SAM3 manga mask scorecard {field_name} is invalid.",
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_canvas_size_invalid",
            f"SAM3 manga mask scorecard {field_name} is invalid.",
        ) from exc
    if number <= 0:
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_canvas_size_invalid",
            f"SAM3 manga mask scorecard {field_name} must be positive.",
        )
    return number


def _thresholds(
    *,
    min_confidence: object,
    min_area_ratio: object,
    max_area_ratio: object,
) -> tuple[float, float, float]:
    confidence = _ratio_threshold(min_confidence, "min_confidence")
    min_area = _nonnegative_threshold(min_area_ratio, "min_area_ratio")
    max_area = _positive_threshold(max_area_ratio, "max_area_ratio")
    if min_area > max_area:
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_threshold_invalid",
            "SAM3 manga mask scorecard area thresholds are invalid.",
        )
    return (confidence, min_area, max_area)


def _ratio_threshold(value: object, field_name: str) -> float:
    number = _finite_float(value, field_name)
    if number < 0.0 or number > 1.0:
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_threshold_invalid",
            f"SAM3 manga mask scorecard {field_name} must be between 0 and 1.",
        )
    return number


def _nonnegative_threshold(value: object, field_name: str) -> float:
    number = _finite_float(value, field_name)
    if number < 0.0:
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_threshold_invalid",
            f"SAM3 manga mask scorecard {field_name} must be non-negative.",
        )
    return number


def _positive_threshold(value: object, field_name: str) -> float:
    number = _finite_float(value, field_name)
    if number <= 0.0:
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_threshold_invalid",
            f"SAM3 manga mask scorecard {field_name} must be positive.",
        )
    return number


def _finite_float(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_threshold_invalid",
            f"SAM3 manga mask scorecard {field_name} is invalid.",
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_threshold_invalid",
            f"SAM3 manga mask scorecard {field_name} is invalid.",
        ) from exc
    if not math.isfinite(number):
        raise Sam3MangaMaskScorecardError(
            "manga_mask_scorecard_threshold_invalid",
            f"SAM3 manga mask scorecard {field_name} must be finite.",
        )
    return number


__all__ = [
    "SAM3_MANGA_MASK_CANDIDATE_SCORECARD_SCHEMA_VERSION",
    "Sam3MangaMaskCandidateScore",
    "Sam3MangaMaskCandidateScoreWarning",
    "Sam3MangaMaskPackageScorecard",
    "Sam3MangaMaskScorecardError",
    "score_comic_mask_package_candidates",
]
