"""UI-independent SAM3 AI Select refine orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .data_model import (
    BlockedReason,
    MaskCandidate,
    MaskCandidateSet,
    OverlayOptions,
    Sam3Prompt,
    Sam3RefineRequest,
    Sam3Warning,
)
from .provider import Sam3MaskProvider
from .selection_bridge import Sam3SelectionBridgeError, Sam3SelectionPromptSpec
from .selection_overlay import Sam3SelectionOverlayModel, build_selection_overlay_model
from .selection_session import Sam3SelectionSession, create_selection_session
from .worker_client import AiSelectionWorkerUnavailable

Sam3SelectionRefineStatus = Literal["completed", "blocked"]

SAM3_SELECTION_REFINE_SCHEMA_VERSION = "sam3.selection_refine.v1"

# Default refine options sent when the caller does not override them: the
# worker picks the prompt-refinement engine when it can and falls back to the
# legacy refine otherwise (LOOMSTROKE_SAM3_PROMPT_REFINEMENT_DESIGN_SPEC §4.1).
# ``autoCleanup`` is consumed client-side at mask materialization (spec §21
# の morphology 段の自動適用) and never reaches the worker.
SAM3_SELECTION_REFINE_DEFAULT_OPTIONS: dict[str, Any] = {
    "strategy": "auto",
    "preset": "balanced",
    "autoCleanup": True,
}


@dataclass(frozen=True, slots=True)
class Sam3SelectionRefineResult:
    """Result payload for refining one AI Select candidate mask."""

    request: Sam3RefineRequest | None
    status: Sam3SelectionRefineStatus
    candidate_set: MaskCandidateSet | None = None
    session: Sam3SelectionSession | None = None
    overlay_model: Sam3SelectionOverlayModel | None = None
    blocked_reasons: tuple[BlockedReason, ...] = field(default_factory=tuple)
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)
    recommended_actions: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_SELECTION_REFINE_SCHEMA_VERSION

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "completed": self.completed,
            "request": self.request.to_dict() if self.request is not None else None,
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


async def refine_selection_candidate(
    provider: Sam3MaskProvider,
    *,
    request_id: str,
    session: Sam3SelectionSession,
    candidate_id: str | None = None,
    prompt_spec: Sam3SelectionPromptSpec | None = None,
    overlay_options: OverlayOptions | None = None,
    options: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Sam3SelectionRefineResult:
    """Refine the selected candidate mask without touching canvas state."""

    request_id = str(request_id).strip()
    if not request_id:
        raise Sam3SelectionBridgeError(
            "request_id_missing",
            "SAM3 refine request requires a request id.",
        )

    candidate, blocked_reasons = _resolve_refine_candidate(session, candidate_id)
    if blocked_reasons:
        return _blocked_result(request=None, blocked_reasons=blocked_reasons)
    if candidate is None:
        return _blocked_result(
            request=None,
            blocked_reasons=(
                _blocked_reason(
                    "candidate_selection_missing",
                    "No SAM3 candidate is selected for refine.",
                    "Select a SAM3 candidate before refining.",
                ),
            ),
        )

    prompt = prompt_spec.to_prompt() if prompt_spec is not None else session.candidate_set.prompt
    request = _build_refine_request(
        request_id,
        session,
        candidate,
        prompt,
        options=options,
        metadata=metadata,
    )

    try:
        candidate_set = await provider.refine(request)
    except AiSelectionWorkerUnavailable as exc:
        blocked_reasons, warnings, recommended_actions = _worker_blocked_payload(exc)
        return _blocked_result(
            request=request,
            blocked_reasons=blocked_reasons,
            warnings=warnings,
            recommended_actions=recommended_actions,
        )

    refined_session = create_selection_session(
        candidate_set,
        session.current_context,
        operation=session.operation,
    )
    overlay_model = build_selection_overlay_model(refined_session, overlay_options)
    return Sam3SelectionRefineResult(
        request=request,
        status="completed",
        candidate_set=candidate_set,
        session=refined_session,
        overlay_model=overlay_model,
        warnings=overlay_model.warnings,
    )


def _build_refine_request(
    request_id: str,
    session: Sam3SelectionSession,
    candidate: MaskCandidate,
    prompt: Sam3Prompt,
    *,
    options: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
) -> Sam3RefineRequest:
    request_metadata = dict(metadata or {})
    request_metadata.update(
        {
            "sourceCandidateSetId": session.candidate_set.set_id,
            "sourceCandidateId": candidate.candidate_id,
        }
    )
    return Sam3RefineRequest(
        request_id=request_id,
        source_ref=session.current_context.to_source_ref(),
        base_mask_asset_id=str(candidate.mask_asset_id).strip(),
        prompt=prompt,
        options=(
            dict(options) if options is not None else dict(SAM3_SELECTION_REFINE_DEFAULT_OPTIONS)
        ),
        metadata=request_metadata,
    )


def _resolve_refine_candidate(
    session: Sam3SelectionSession,
    candidate_id: str | None,
) -> tuple[MaskCandidate | None, tuple[BlockedReason, ...]]:
    normalized_id = _clean_candidate_id(
        candidate_id if candidate_id is not None else session.selected_candidate_id
    )
    if normalized_id is None:
        return (
            None,
            (
                _blocked_reason(
                    "candidate_selection_missing",
                    "No SAM3 candidate is selected for refine.",
                    "Select a SAM3 candidate before refining.",
                ),
            ),
        )

    if normalized_id in session.rejected_candidate_ids:
        return (
            None,
            (
                _blocked_reason(
                    "candidate_unavailable",
                    "SAM3 candidate is not available for refine.",
                    "Select an available SAM3 candidate before refining.",
                ),
            ),
        )

    candidate = _find_candidate(session.candidate_set, normalized_id)
    if candidate is None:
        return (
            None,
            (
                _blocked_reason(
                    "candidate_unavailable",
                    "SAM3 candidate is not available for refine.",
                    "Select an available SAM3 candidate before refining.",
                ),
            ),
        )

    mask_asset_id = str(candidate.mask_asset_id or "").strip()
    if not mask_asset_id:
        return (
            None,
            (
                _blocked_reason(
                    "candidate_mask_missing",
                    "SAM3 candidate does not reference a mask asset for refine.",
                    "Run AI Select again before refining the candidate.",
                ),
            ),
        )

    return candidate, ()


def _find_candidate(
    candidate_set: MaskCandidateSet,
    candidate_id: str,
) -> MaskCandidate | None:
    for candidate in candidate_set.candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    return None


def _clean_candidate_id(value: str | None) -> str | None:
    if value is None:
        return None
    candidate_id = str(value).strip()
    return candidate_id or None


def _blocked_result(
    *,
    request: Sam3RefineRequest | None,
    blocked_reasons: tuple[BlockedReason, ...],
    warnings: tuple[Sam3Warning, ...] = (),
    recommended_actions: tuple[str, ...] | None = None,
) -> Sam3SelectionRefineResult:
    return Sam3SelectionRefineResult(
        request=request,
        status="blocked",
        blocked_reasons=tuple(blocked_reasons),
        warnings=tuple(warnings),
        recommended_actions=(
            tuple(recommended_actions)
            if recommended_actions is not None
            else _actions_from_reasons(tuple(blocked_reasons))
        ),
    )


def _worker_blocked_payload(
    exc: AiSelectionWorkerUnavailable,
) -> tuple[tuple[BlockedReason, ...], tuple[Sam3Warning, ...], tuple[str, ...]]:
    readiness = exc.readiness
    if readiness is None:
        reason = _fallback_worker_blocked_reason()
        return (reason,), (), _actions_from_reasons((reason,))

    blocked_reasons = readiness.blocked_reasons
    if not blocked_reasons:
        blocked_reasons = (_fallback_worker_blocked_reason(),)
    recommended_actions = readiness.recommended_actions or _actions_from_reasons(blocked_reasons)
    return tuple(blocked_reasons), tuple(readiness.warnings), tuple(recommended_actions)


def _fallback_worker_blocked_reason() -> BlockedReason:
    return _blocked_reason(
        "worker_client_unavailable",
        "SAM3 worker client is unavailable.",
        "Configure a SAM3 worker before refining candidates.",
    )


def _blocked_reason(code: str, message: str, action: str) -> BlockedReason:
    return BlockedReason(code=code, message=message, action=action)


def _actions_from_reasons(reasons: tuple[BlockedReason, ...]) -> tuple[str, ...]:
    return tuple(reason.action for reason in reasons if reason.action)


__all__ = [
    "SAM3_SELECTION_REFINE_DEFAULT_OPTIONS",
    "SAM3_SELECTION_REFINE_SCHEMA_VERSION",
    "Sam3SelectionRefineResult",
    "Sam3SelectionRefineStatus",
    "refine_selection_candidate",
]
