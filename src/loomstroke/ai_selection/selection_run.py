"""UI-independent SAM3 AI Select request orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .data_model import (
    BlockedReason,
    MaskCandidateSet,
    OverlayOptions,
    Sam3SegmentRequest,
    Sam3Warning,
)
from .provider import Sam3MaskProvider
from .selection_bridge import (
    Sam3SelectionContext,
    Sam3SelectionPromptSpec,
    build_selection_segment_request,
    normalize_selection_apply_operation,
)
from .selection_overlay import Sam3SelectionOverlayModel, build_selection_overlay_model
from .selection_session import Sam3SelectionSession, create_selection_session
from .worker_client import AiSelectionWorkerUnavailable

Sam3SelectionRunStatus = Literal["completed", "blocked"]

SAM3_SELECTION_RUN_SCHEMA_VERSION = "sam3.selection_run.v1"


@dataclass(frozen=True, slots=True)
class Sam3SelectionRunResult:
    """Result payload for one UI-independent SAM3 AI Select run."""

    request: Sam3SegmentRequest
    status: Sam3SelectionRunStatus
    candidate_set: MaskCandidateSet | None = None
    session: Sam3SelectionSession | None = None
    overlay_model: Sam3SelectionOverlayModel | None = None
    blocked_reasons: tuple[BlockedReason, ...] = field(default_factory=tuple)
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)
    recommended_actions: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_SELECTION_RUN_SCHEMA_VERSION

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "completed": self.completed,
            "request": self.request.to_dict(),
            "candidateSet": (
                self.candidate_set.to_dict() if self.candidate_set is not None else None
            ),
            "session": self.session.to_dict() if self.session is not None else None,
            "overlayModel": (
                self.overlay_model.to_dict() if self.overlay_model is not None else None
            ),
            "blockedReasons": [reason.to_dict() for reason in self.blocked_reasons],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "recommendedActions": list(self.recommended_actions),
        }


async def run_selection_request(
    provider: Sam3MaskProvider,
    *,
    request_id: str,
    context: Sam3SelectionContext,
    prompt_spec: Sam3SelectionPromptSpec,
    operation: str = "replace",
    overlay_options: OverlayOptions | None = None,
    max_candidates: int = 16,
    min_score: float = 0.2,
    mask_threshold: float = 0.5,
    split_disconnected_instances: bool = True,
    auto_mask_cleanup: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> Sam3SelectionRunResult:
    normalized_operation = normalize_selection_apply_operation(operation)
    request = build_selection_segment_request(
        request_id,
        context,
        prompt_spec,
        max_candidates=max_candidates,
        min_score=min_score,
        mask_threshold=mask_threshold,
        split_disconnected_instances=split_disconnected_instances,
        auto_mask_cleanup=auto_mask_cleanup,
        metadata=metadata,
    )

    try:
        candidate_set = await provider.segment(request)
    except AiSelectionWorkerUnavailable as exc:
        blocked_reasons, warnings, recommended_actions = _blocked_payload(exc)
        return Sam3SelectionRunResult(
            request=request,
            status="blocked",
            blocked_reasons=blocked_reasons,
            warnings=warnings,
            recommended_actions=recommended_actions,
        )

    session = create_selection_session(
        candidate_set,
        context,
        operation=normalized_operation,
    )
    overlay_model = build_selection_overlay_model(session, overlay_options)
    return Sam3SelectionRunResult(
        request=request,
        status="completed",
        candidate_set=candidate_set,
        session=session,
        overlay_model=overlay_model,
        warnings=overlay_model.warnings,
    )


def _blocked_payload(
    exc: AiSelectionWorkerUnavailable,
) -> tuple[tuple[BlockedReason, ...], tuple[Sam3Warning, ...], tuple[str, ...]]:
    readiness = exc.readiness
    if readiness is None:
        reason = _fallback_blocked_reason()
        return (reason,), (), _actions_from_reasons((reason,))

    blocked_reasons = readiness.blocked_reasons
    if not blocked_reasons:
        blocked_reasons = (_fallback_blocked_reason(),)
    recommended_actions = readiness.recommended_actions or _actions_from_reasons(blocked_reasons)
    return tuple(blocked_reasons), tuple(readiness.warnings), tuple(recommended_actions)


def _fallback_blocked_reason() -> BlockedReason:
    return BlockedReason(
        code="worker_client_unavailable",
        message="SAM3 worker client is unavailable.",
        action="Configure a SAM3 worker before running segmentation.",
    )


def _actions_from_reasons(reasons: tuple[BlockedReason, ...]) -> tuple[str, ...]:
    return tuple(reason.action for reason in reasons if reason.action)


__all__ = [
    "SAM3_SELECTION_RUN_SCHEMA_VERSION",
    "Sam3SelectionRunResult",
    "Sam3SelectionRunStatus",
    "run_selection_request",
]
