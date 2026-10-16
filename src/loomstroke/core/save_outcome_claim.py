"""Pending-claim matching for DOC_SAVED → DOC_SAVE_OUTCOME (SAVE-ASYNC-REV-01).

A built-in save announces its authoritative outcome stage by emitting
``DOC_SAVED`` with ``save_outcome_pending=True``; the matching
``DOC_SAVE_OUTCOME`` follows synchronously in the same save call.
Consumers (MainWindow workflow coordinator, AutosaveManager) record a
:class:`PendingSaveOutcomeClaim` from the pending payload and later accept
a final payload only when it is well-formed AND matches the claim on
normalized path + captured document epoch + captured revision.  Anything
else — unsolicited, malformed, mismatched, or duplicated finals — is
rejected fail-closed via :func:`evaluate_final_save_outcome`.

EventBus delivery is synchronous and DocumentService is single-flight, so
(path, captured_epoch, captured_revision) identifies the save without a
dedicated save id.  This module is pure Python (no Qt / IO / logging) so
both consumers share one validation truth and cannot drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = (
    "UNMATCHABLE_CLAIM",
    "FinalSaveOutcomeDecision",
    "PendingSaveOutcomeClaim",
    "SaveOutcomeAnnouncement",
    "claim_from_pending_payload",
    "classify_save_outcome_announcement",
    "evaluate_final_save_outcome",
)

_MISSING = object()
"""Sentinel distinguishing an absent payload field from any present value."""


def _strict_int(value: Any) -> int | None:
    """Return ``value`` as an int, rejecting bools (bool is an int subclass)."""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _normalized_path(value: Any) -> str | None:
    """Normalize a payload path for claim comparison; None when unusable."""

    if not isinstance(value, str) or not value:
        return None
    try:
        return str(Path(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class PendingSaveOutcomeClaim:
    """What a pending DOC_SAVED promised: identifies the final to accept.

    Fields are None when the pending payload was missing/invalid on that
    key — the claim still exists (the service declared a final stage), but
    no final can match it, so everything stays fail-closed.
    """

    path: str | None
    captured_epoch: int | None
    captured_revision: int | None


UNMATCHABLE_CLAIM = PendingSaveOutcomeClaim(path=None, captured_epoch=None, captured_revision=None)
"""Claim recorded for a malformed announcement: keeps the consumer in the
fail-closed pending/unsettled state while guaranteeing no final — however
well-formed — can ever settle it."""


class SaveOutcomeAnnouncement(Enum):
    """Three-way classification of a manual DOC_SAVED's pending marker.

    - ``LEGACY``    — ``save_outcome_pending`` is absent: a custom
      DocumentService payload; apply immediately with legacy defaults.
    - ``PENDING``   — the field is the bool ``True``: a well-formed
      announcement; record the claim and wait for the matching final.
    - ``MALFORMED`` — the field is present but not the bool ``True``
      (False / None / 0 / 1 / "" / "true" / ...): never the legacy
      fallback; enter the fail-closed unsettled state.
    """

    LEGACY = "legacy"
    PENDING = "pending"
    MALFORMED = "malformed"


def classify_save_outcome_announcement(payload: Any) -> SaveOutcomeAnnouncement:
    """Classify a manual DOC_SAVED payload's ``save_outcome_pending`` marker.

    Field presence and value are deliberately distinguished — collapsing
    them via ``bool(get(..., False))`` would route a malformed marker into
    the legacy immediate-apply path.
    """

    if isinstance(payload, Mapping):
        value = payload.get("save_outcome_pending", _MISSING)
    else:
        value = getattr(payload, "save_outcome_pending", _MISSING)
    if value is _MISSING:
        return SaveOutcomeAnnouncement.LEGACY
    if value is True:
        return SaveOutcomeAnnouncement.PENDING
    return SaveOutcomeAnnouncement.MALFORMED


@dataclass(frozen=True, slots=True)
class FinalSaveOutcomeDecision:
    """Validation result for one DOC_SAVE_OUTCOME payload.

    ``accepted`` is True only for a well-formed final matching the claim;
    ``reason`` is a stable slug for (secret-free) diagnostics.  The outcome
    fields are meaningful only when accepted.
    """

    accepted: bool
    reason: str
    path: str | None = None
    same_document: bool = False
    covers_latest: bool = False


def claim_from_pending_payload(payload: Any) -> PendingSaveOutcomeClaim:
    """Record the claim announced by a pending DOC_SAVED payload."""

    if not isinstance(payload, Mapping):
        return PendingSaveOutcomeClaim(path=None, captured_epoch=None, captured_revision=None)
    return PendingSaveOutcomeClaim(
        path=_normalized_path(payload.get("path")),
        captured_epoch=_strict_int(payload.get("captured_document_epoch")),
        captured_revision=_strict_int(payload.get("captured_revision")),
    )


def _final_rejection_reason(
    payload: Mapping[str, Any],
    claim: PendingSaveOutcomeClaim,
) -> str | None:
    """First failing strict check for a final payload; None when valid.

    Checks run in declaration order so malformed payloads report their
    shape problem before any claim-mismatch diagnosis.
    """

    path = _normalized_path(payload.get("path"))
    epoch = _strict_int(payload.get("captured_document_epoch"))
    revision = _strict_int(payload.get("captured_revision"))
    checks: tuple[tuple[bool, str], ...] = (
        # Only the bool ``False`` singleton is acceptable: a missing
        # field, True, or falsy non-bool stand-ins (0 / None / "" /
        # "false") all reject — truthiness would silently accept them.
        (payload.get("autosave", _MISSING) is not False, "invalid_autosave"),
        (path is None, "invalid_path"),
        (not isinstance(payload.get("save_same_document"), bool), "invalid_same_document"),
        (not isinstance(payload.get("save_covers_latest"), bool), "invalid_covers_latest"),
        (epoch is None, "invalid_epoch"),
        (revision is None, "invalid_revision"),
        (claim.path is None or path != claim.path, "path_mismatch"),
        (claim.captured_epoch is None or epoch != claim.captured_epoch, "epoch_mismatch"),
        (
            claim.captured_revision is None or revision != claim.captured_revision,
            "revision_mismatch",
        ),
    )
    for failed, reason in checks:
        if failed:
            return reason
    return None


def evaluate_final_save_outcome(
    payload: Any,
    claim: PendingSaveOutcomeClaim | None,
) -> FinalSaveOutcomeDecision:
    """Strictly validate a DOC_SAVE_OUTCOME payload against the claim.

    Fail-closed: any missing/mistyped required field, a missing claim
    (unsolicited or duplicated final), or a claim mismatch on normalized
    path / captured epoch / captured revision rejects the payload.
    """

    if claim is None:
        return FinalSaveOutcomeDecision(accepted=False, reason="no_pending_claim")
    if not isinstance(payload, Mapping):
        return FinalSaveOutcomeDecision(accepted=False, reason="payload_not_mapping")
    reason = _final_rejection_reason(payload, claim)
    if reason is not None:
        return FinalSaveOutcomeDecision(accepted=False, reason=reason)
    return FinalSaveOutcomeDecision(
        accepted=True,
        reason="ok",
        path=_normalized_path(payload.get("path")),
        same_document=bool(payload.get("save_same_document")),
        covers_latest=bool(payload.get("save_covers_latest")),
    )
