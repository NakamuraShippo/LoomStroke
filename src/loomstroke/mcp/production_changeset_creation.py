"""Checked draft creation and refusal of repeated Comic changeset application."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import production as pct
from .production_changeset_state import ChangesetState, _detached
from .production_record_commit import reject_pending_apply


def _blocked_applied_comic(owner: Any, record: Mapping[str, Any]) -> dict[str, Any] | None:
    """PCT status is independent of Canvas Undo; reapply never guesses its state."""
    if record.get("status") != "applied" or not owner._has_comic_changeset_operations(
        record.get("operations")
    ):
        return None
    return pct.pct_response(
        {"changeset": record},
        blocked=True,
        blocked_reasons=["comic_changeset_already_applied"],
    )


async def _create_changeset(
    owner: Any,
    candidate: Mapping[str, Any],
    *,
    confirmation_name: str,
    summary: Mapping[str, Any] | None = None,
    warnings: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Preserve recovery metadata and own the target before and after confirmation.

    The ordinary store upsert remains unchanged. This mutation boundary alone
    requires strict reads, preserves fresh other IDs and confirms its own write.
    A changed/unknown target is retained rather than inferred to be an empty slot.
    """
    desired = _detached(candidate)
    state = ChangesetState._capture_for_creation(owner, str(desired["changesetId"]))
    existing = state._existing_snapshot()
    if existing is not None:
        reject_pending_apply(existing)
        blocked = _blocked_applied_comic(owner, existing)
        if blocked is not None:
            return blocked
    await owner._require_pct_confirmation(confirmation_name, _detached(desired), hard=True)
    state.ensure_current()
    saved = state.write(desired)
    data: dict[str, Any] = {"changeset": saved}
    if summary is not None:
        data["summary"] = summary
    return pct.pct_response(data, warnings=warnings, requires_confirmation=True)
