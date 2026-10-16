"""Pure runtime session state for SAM3 AI Select candidates."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, cast

from .data_model import BlockedReason, MaskCandidate, MaskCandidateSet
from .selection_bridge import (
    Sam3SelectionApplyPlan,
    Sam3SelectionBridgeError,
    Sam3SelectionContext,
    SelectionApplyOperation,
    build_selection_apply_plan,
    normalize_selection_apply_operation,
)

SAM3_SELECTION_SESSION_SCHEMA_VERSION = "sam3.selection_session.v1"


class Sam3SelectionSessionError(ValueError):
    """Raised when a SAM3 candidate session cannot be updated safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3SelectionSession:
    """Runtime-only selection state for a SAM3 candidate set."""

    candidate_set: MaskCandidateSet
    current_context: Sam3SelectionContext
    operation: SelectionApplyOperation = "replace"
    selected_candidate_id: str | None = None
    rejected_candidate_ids: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_SELECTION_SESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            operation = normalize_selection_apply_operation(str(self.operation))
        except Sam3SelectionBridgeError as exc:
            raise Sam3SelectionSessionError(exc.code, str(exc)) from exc

        self.current_context.to_source_ref()
        rejected_candidate_ids = _normalize_rejected_candidate_ids(
            self.candidate_set,
            self.rejected_candidate_ids,
        )
        selected_candidate_id = _normalize_selected_candidate_id(
            self.candidate_set,
            self.selected_candidate_id,
            rejected_candidate_ids,
        )

        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "rejected_candidate_ids", rejected_candidate_ids)
        object.__setattr__(self, "selected_candidate_id", selected_candidate_id)

    @property
    def available_candidate_ids(self) -> tuple[str, ...]:
        rejected = set(self.rejected_candidate_ids)
        return tuple(
            candidate.candidate_id
            for candidate in self.candidate_set.candidates
            if candidate.candidate_id and candidate.candidate_id not in rejected
        )

    @property
    def active_candidate(self) -> MaskCandidate | None:
        selected_id = self.selected_candidate_id
        if selected_id is None:
            return None
        return _find_candidate(self.candidate_set, selected_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "candidateSet": self.candidate_set.to_dict(),
            "currentContext": _context_to_dict(self.current_context),
            "operation": self.operation,
            "selectedCandidateId": self.selected_candidate_id,
            "rejectedCandidateIds": list(self.rejected_candidate_ids),
            "availableCandidateIds": list(self.available_candidate_ids),
        }


def create_selection_session(
    candidate_set: MaskCandidateSet,
    current_context: Sam3SelectionContext,
    *,
    operation: str = "replace",
    selected_candidate_id: str | None = None,
    rejected_candidate_ids: tuple[str, ...] = (),
) -> Sam3SelectionSession:
    return Sam3SelectionSession(
        candidate_set=candidate_set,
        current_context=current_context,
        operation=cast("SelectionApplyOperation", operation),
        selected_candidate_id=selected_candidate_id,
        rejected_candidate_ids=tuple(rejected_candidate_ids),
    )


def select_session_candidate(
    session: Sam3SelectionSession,
    candidate_id: str,
) -> Sam3SelectionSession:
    candidate_id = _clean_candidate_id(candidate_id)
    if not _is_available_candidate_id(session, candidate_id):
        raise Sam3SelectionSessionError(
            "candidate_unavailable",
            "SAM3 candidate is not available for selection.",
        )
    return replace(session, selected_candidate_id=candidate_id)


def select_next_session_candidate(session: Sam3SelectionSession) -> Sam3SelectionSession:
    return _select_relative_candidate(session, step=1)


def select_previous_session_candidate(session: Sam3SelectionSession) -> Sam3SelectionSession:
    return _select_relative_candidate(session, step=-1)


def reject_session_candidate(
    session: Sam3SelectionSession,
    candidate_id: str | None = None,
) -> Sam3SelectionSession:
    rejected_id = _clean_candidate_id(candidate_id or session.selected_candidate_id)
    if not rejected_id or _find_candidate(session.candidate_set, rejected_id) is None:
        raise Sam3SelectionSessionError(
            "candidate_unavailable",
            "SAM3 candidate is not available for rejection.",
        )

    rejected_ids = _normalize_rejected_candidate_ids(
        session.candidate_set,
        (*session.rejected_candidate_ids, rejected_id),
    )
    selected_id = session.selected_candidate_id
    if selected_id == rejected_id or selected_id is None or selected_id in rejected_ids:
        selected_id = _first_available_after(session.candidate_set, rejected_id, rejected_ids)

    return replace(
        session,
        selected_candidate_id=selected_id,
        rejected_candidate_ids=rejected_ids,
    )


def build_session_apply_plan(session: Sam3SelectionSession) -> Sam3SelectionApplyPlan:
    candidate_id = session.selected_candidate_id
    if candidate_id is None:
        return Sam3SelectionApplyPlan(
            candidate_set_id=session.candidate_set.set_id,
            candidate_id="",
            mask_asset_id=None,
            operation=session.operation,
            source_ref=session.candidate_set.source_ref,
            selection_bounds=None,
            status="blocked",
            blocked_reasons=(
                BlockedReason(
                    code="candidate_selection_missing",
                    message="No SAM3 candidate is selected.",
                    action="Select or regenerate a SAM3 candidate before applying.",
                ),
            ),
        )

    return build_selection_apply_plan(
        session.candidate_set,
        candidate_id,
        session.current_context,
        operation=session.operation,
    )


def _select_relative_candidate(
    session: Sam3SelectionSession,
    *,
    step: int,
) -> Sam3SelectionSession:
    available_ids = session.available_candidate_ids
    if not available_ids:
        return replace(session, selected_candidate_id=None)

    selected_id = session.selected_candidate_id
    if selected_id not in available_ids:
        selected_id = available_ids[0] if step > 0 else available_ids[-1]
        return replace(session, selected_candidate_id=selected_id)

    current_index = available_ids.index(selected_id)
    next_index = (current_index + step) % len(available_ids)
    return replace(session, selected_candidate_id=available_ids[next_index])


def _first_available_after(
    candidate_set: MaskCandidateSet,
    rejected_id: str,
    rejected_candidate_ids: tuple[str, ...],
) -> str | None:
    candidate_ids = _candidate_ids(candidate_set)
    if not candidate_ids:
        return None
    rejected = set(rejected_candidate_ids)
    try:
        start_index = candidate_ids.index(rejected_id) + 1
    except ValueError:
        start_index = 0
    for offset in range(len(candidate_ids)):
        candidate_id = candidate_ids[(start_index + offset) % len(candidate_ids)]
        if candidate_id not in rejected:
            return candidate_id
    return None


def _normalize_selected_candidate_id(
    candidate_set: MaskCandidateSet,
    selected_candidate_id: str | None,
    rejected_candidate_ids: tuple[str, ...],
) -> str | None:
    rejected = set(rejected_candidate_ids)
    selected_id = _clean_candidate_id(selected_candidate_id)
    candidate_ids = _candidate_ids(candidate_set)
    if selected_id in candidate_ids and selected_id not in rejected:
        return selected_id
    for candidate_id in candidate_ids:
        if candidate_id not in rejected:
            return candidate_id
    return None


def _normalize_rejected_candidate_ids(
    candidate_set: MaskCandidateSet,
    rejected_candidate_ids: tuple[str, ...],
) -> tuple[str, ...]:
    raw_rejected = {
        candidate.candidate_id
        for candidate in candidate_set.rejected_candidates
        if candidate.candidate_id
    }
    raw_rejected.update(
        _clean_candidate_id(candidate_id) for candidate_id in rejected_candidate_ids
    )
    return tuple(
        candidate_id
        for candidate_id in _candidate_ids(candidate_set)
        if candidate_id in raw_rejected
    )


def _is_available_candidate_id(session: Sam3SelectionSession, candidate_id: str) -> bool:
    return candidate_id in session.available_candidate_ids


def _find_candidate(candidate_set: MaskCandidateSet, candidate_id: str) -> MaskCandidate | None:
    for candidate in candidate_set.candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    return None


def _candidate_ids(candidate_set: MaskCandidateSet) -> tuple[str, ...]:
    return tuple(
        candidate.candidate_id for candidate in candidate_set.candidates if candidate.candidate_id
    )


def _clean_candidate_id(candidate_id: str | None) -> str:
    return str(candidate_id or "").strip()


def _context_to_dict(context: Sam3SelectionContext) -> dict[str, Any]:
    source_ref = context.to_source_ref().to_dict()
    source_ref["availableLayerIds"] = list(context.available_layer_ids)
    return source_ref


__all__ = [
    "SAM3_SELECTION_SESSION_SCHEMA_VERSION",
    "Sam3SelectionSession",
    "Sam3SelectionSessionError",
    "build_session_apply_plan",
    "create_selection_session",
    "reject_session_candidate",
    "select_next_session_candidate",
    "select_previous_session_candidate",
    "select_session_candidate",
]
