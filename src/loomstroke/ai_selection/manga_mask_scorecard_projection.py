"""Production-friendly projections for SAM3 manga mask scorecards."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from .manga_mask_scorecard import (
    Sam3MangaMaskCandidateScore,
    Sam3MangaMaskCandidateScoreWarning,
    Sam3MangaMaskPackageScorecard,
)

SAM3_MANGA_MASK_SCORECARD_PRODUCTION_PROJECTION_SCHEMA_VERSION = (
    "sam3.manga_mask_scorecard_production_projection.v1"
)

DecisionRecommendation = Literal["keep", "manual_review", "rework", "reject"]
CheckStatus = Literal["pass", "info", "warning", "hard_fail"]


class Sam3MangaMaskScorecardProjectionError(ValueError):
    """Raised when a scorecard cannot be projected for production surfaces."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskScorecardProductionIssue:
    """One production-facing scorecard issue."""

    check: str
    message: str
    severity: str

    def to_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskScorecardProductionCheck:
    """One deterministic production-facing check result."""

    check: str
    status: CheckStatus
    score: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status,
            "score": self.score,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskScorecardProductionCandidate:
    """Production-facing decision summary for one SAM3 manga mask candidate."""

    candidate_id: str
    scope: str
    panel_id: str | None
    category: str
    accepted: bool
    ready: bool
    score: int
    decision_recommendation: DecisionRecommendation
    hard_failures: tuple[Sam3MangaMaskScorecardProductionIssue, ...]
    warnings: tuple[Sam3MangaMaskScorecardProductionIssue, ...]
    check_results: tuple[Sam3MangaMaskScorecardProductionCheck, ...]
    recommended_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "scope": self.scope,
            "panelId": self.panel_id,
            "category": self.category,
            "accepted": self.accepted,
            "ready": self.ready,
            "score": self.score,
            "decisionRecommendation": self.decision_recommendation,
            "hardFailures": [issue.to_dict() for issue in self.hard_failures],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "checkResults": [result.to_dict() for result in self.check_results],
            "recommendedActions": list(self.recommended_actions),
        }


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskScorecardProductionProjection:
    """Production-friendly projection of a SAM3 manga mask scorecard."""

    package_id: str
    source_page_id: str
    job_id: str | None
    artifact_id: str | None
    quality_profile: str
    candidate_set_id: str
    selected_candidate_id: str | None
    ok: bool
    candidate_count: int
    accepted_candidate_count: int
    ready_candidate_count: int
    results: tuple[Sam3MangaMaskScorecardProductionCandidate, ...]
    recommended_actions: tuple[str, ...]
    domain: str = "comic_page"
    schema_version: str = SAM3_MANGA_MASK_SCORECARD_PRODUCTION_PROJECTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "packageId": self.package_id,
            "sourcePageId": self.source_page_id,
            "jobId": self.job_id,
            "artifactId": self.artifact_id,
            "domain": self.domain,
            "qualityProfile": self.quality_profile,
            "candidateSetId": self.candidate_set_id,
            "selectedCandidateId": self.selected_candidate_id,
            "ok": self.ok,
            "candidateCount": self.candidate_count,
            "acceptedCandidateCount": self.accepted_candidate_count,
            "readyCandidateCount": self.ready_candidate_count,
            "results": [result.to_dict() for result in self.results],
            "recommendedActions": list(self.recommended_actions),
        }


def build_manga_mask_scorecard_production_projection(
    scorecard: Sam3MangaMaskPackageScorecard,
    *,
    job_id: object | None = None,
    artifact_id: object | None = None,
    quality_profile: object = "sam3_manga_mask_quality",
) -> Sam3MangaMaskScorecardProductionProjection:
    """Project a SAM3 manga mask scorecard into a production decision payload."""

    if not isinstance(scorecard, Sam3MangaMaskPackageScorecard):
        raise Sam3MangaMaskScorecardProjectionError(
            "manga_mask_scorecard_projection_scorecard_invalid",
            "SAM3 manga mask scorecard projection requires a package scorecard.",
        )

    normalized_quality_profile = _quality_profile(quality_profile)
    results = tuple(_project_candidate(candidate) for candidate in scorecard.candidates)
    return Sam3MangaMaskScorecardProductionProjection(
        package_id=scorecard.package_id,
        source_page_id=scorecard.source_page_id,
        job_id=_optional_text(job_id),
        artifact_id=_optional_text(artifact_id),
        quality_profile=normalized_quality_profile,
        candidate_set_id=f"sam3_manga_mask:{scorecard.package_id}",
        selected_candidate_id=_selected_candidate_id(results),
        ok=scorecard.ok,
        candidate_count=scorecard.candidate_count,
        accepted_candidate_count=scorecard.accepted_candidate_count,
        ready_candidate_count=scorecard.ready_candidate_count,
        results=results,
        recommended_actions=_stable_actions(scorecard.recommended_actions),
    )


def _project_candidate(
    candidate: Sam3MangaMaskCandidateScore,
) -> Sam3MangaMaskScorecardProductionCandidate:
    hard_failures, warnings, checks = _issues_and_checks(candidate.warnings)
    score = _production_score(candidate.quality_score, hard_failures=bool(hard_failures))
    recommendation = _decision_recommendation(
        candidate,
        score=score,
        has_hard_failures=bool(hard_failures),
        has_warnings=bool(warnings),
    )
    return Sam3MangaMaskScorecardProductionCandidate(
        candidate_id=candidate.candidate_id,
        scope=candidate.scope,
        panel_id=candidate.panel_id,
        category=candidate.category,
        accepted=candidate.accepted,
        ready=candidate.ready,
        score=score,
        decision_recommendation=recommendation,
        hard_failures=hard_failures,
        warnings=warnings,
        check_results=checks,
        recommended_actions=_stable_actions(candidate.recommended_actions),
    )


def _issues_and_checks(
    score_warnings: tuple[Sam3MangaMaskCandidateScoreWarning, ...],
) -> tuple[
    tuple[Sam3MangaMaskScorecardProductionIssue, ...],
    tuple[Sam3MangaMaskScorecardProductionIssue, ...],
    tuple[Sam3MangaMaskScorecardProductionCheck, ...],
]:
    hard_failures: list[Sam3MangaMaskScorecardProductionIssue] = []
    warnings: list[Sam3MangaMaskScorecardProductionIssue] = []
    checks: list[Sam3MangaMaskScorecardProductionCheck] = []
    for warning in score_warnings:
        issue = Sam3MangaMaskScorecardProductionIssue(
            check=warning.code,
            message=warning.message,
            severity=warning.severity,
        )
        status, check_score = _check_status_and_score(warning)
        checks.append(
            Sam3MangaMaskScorecardProductionCheck(
                check=warning.code,
                status=status,
                score=check_score,
                message=warning.message,
            )
        )
        if warning.severity == "error":
            hard_failures.append(issue)
        else:
            warnings.append(issue)
    return (tuple(hard_failures), tuple(warnings), tuple(checks))


def _check_status_and_score(
    warning: Sam3MangaMaskCandidateScoreWarning,
) -> tuple[CheckStatus, int]:
    if warning.severity == "error":
        return ("hard_fail", 0)
    if warning.severity == "warning":
        return ("warning", 70)
    return ("info", 100)


def _production_score(quality_score: float, *, hard_failures: bool) -> int:
    score = round(_clamped_ratio(quality_score) * 100)
    if hard_failures:
        return min(score, 49)
    return score


def _decision_recommendation(
    candidate: Sam3MangaMaskCandidateScore,
    *,
    score: int,
    has_hard_failures: bool,
    has_warnings: bool,
) -> DecisionRecommendation:
    if has_hard_failures:
        return "reject"
    if score < 60:
        return "rework"
    if has_warnings:
        return "manual_review"
    if candidate.ready:
        return "keep"
    return "manual_review"


def _selected_candidate_id(
    results: tuple[Sam3MangaMaskScorecardProductionCandidate, ...],
) -> str | None:
    keepable = [result for result in results if result.decision_recommendation == "keep"]
    if not keepable:
        return None
    return max(keepable, key=lambda result: result.score).candidate_id


def _clamped_ratio(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(1.0, number))


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _quality_profile(value: object) -> str:
    if value is None or isinstance(value, bool):
        raise Sam3MangaMaskScorecardProjectionError(
            "manga_mask_scorecard_projection_quality_profile_invalid",
            "SAM3 manga mask scorecard projection quality profile is invalid.",
        )
    text = str(value).strip()
    if not text:
        raise Sam3MangaMaskScorecardProjectionError(
            "manga_mask_scorecard_projection_quality_profile_invalid",
            "SAM3 manga mask scorecard projection quality profile must be non-empty.",
        )
    return text


def _stable_actions(actions: tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    for action in actions:
        text = str(action).strip()
        if text and text not in unique:
            unique.append(text)
    return tuple(unique)


__all__ = [
    "SAM3_MANGA_MASK_SCORECARD_PRODUCTION_PROJECTION_SCHEMA_VERSION",
    "Sam3MangaMaskScorecardProductionCandidate",
    "Sam3MangaMaskScorecardProductionCheck",
    "Sam3MangaMaskScorecardProductionIssue",
    "Sam3MangaMaskScorecardProductionProjection",
    "Sam3MangaMaskScorecardProjectionError",
    "build_manga_mask_scorecard_production_projection",
]
