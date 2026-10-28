"""Owned record-only PCT commits and confirmed recovery through existing rollback."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, NoReturn

from . import production as pct
from .production_changeset_state import ChangesetState
from .production_record_changeset import (
    _safe,
    _same,
    _write_step,
    checked_receipts,
    prepare_record_operations,
    prepare_record_rollback,
    record_progress,
)
from .types import McpStructuredError, ReasonCode

JOURNAL_KEY = "_loomstrokeRecordRecovery"
_NAMESPACE = "loomstroke.pct.record_recovery"
_KINDS = {"mark_artifact_decision", "add_bible_rule"}


def _refuse(reason: str, *, pending: bool = False) -> NoReturn:
    raise McpStructuredError(
        ReasonCode.BACKEND_FAILED if pending else ReasonCode.CONFLICT,
        "Production record changeset could not be completed safely.",
        details={"reason": reason},
    ) from None


def reject_pending_apply(record: Mapping[str, Any]) -> None:
    if JOURNAL_KEY in record:
        _refuse("record_recovery_required")


def _steps(receipts: list[dict[str, Any]], restore: bool) -> list[dict[str, Any]]:
    result = [row for row in receipts if row["recordType"] != "noop"]
    return list(reversed(result)) if restore else result


def _finished(base: Any, receipts: Any, restore: bool, stamp: str) -> dict[str, Any]:
    result: dict[str, Any] = _safe(base)
    result["status"] = "rolled_back" if restore else "applied"
    result["rolledBackAt" if restore else "appliedAt"] = stamp
    result["updatedAt"] = stamp
    if not restore:
        result["appliedRecords"] = _safe(receipts)
    return result


def _journal(base: Any, receipts: Any, restore: bool) -> dict[str, Any]:
    return {
        "namespace": _NAMESPACE,
        "journalVersion": 1,
        "direction": "rollback" if restore else "apply",
        "phase": "working",
        "beforeChangeset": _safe(base),
        "recordReceipts": _safe(receipts),
        "completedAt": None,
    }


def _with_journal(base: Any, journal: Any) -> dict[str, Any]:
    result: dict[str, Any] = _safe(base)
    result[JOURNAL_KEY] = _safe(journal)
    return result


def _reply(record: Any, receipts: Any, restore: bool) -> dict[str, Any]:
    return pct.pct_response(
        {"changeset": record, "rolledBackRecords" if restore else "appliedRecords": receipts},
        requires_confirmation=True,
    )


def _cleanup(state: ChangesetState) -> dict[str, Any]:
    result = state.snapshot()
    result.pop(JOURNAL_KEY)
    try:
        return state.write(result)
    except Exception:
        _refuse("record_finalization_pending", pending=True)


def _publish_finished(
    state: ChangesetState, base: Any, receipts: Any, restore: bool, journal: Any
) -> None:
    stamp = datetime.now(UTC).isoformat()
    finished_journal = _safe(journal)
    finished_journal.update(phase="finished", completedAt=stamp)
    state.write(_with_journal(_finished(base, receipts, restore, stamp), finished_journal))


def _compensate(
    state: ChangesetState,
    store: Any,
    base: Any,
    receipts: Any,
    restore: bool,
    limit: int | None,
) -> list[dict[str, Any]]:
    guard = state.ensure_current
    progress, _, _ = record_progress(store, receipts, restore=restore, limit=limit, guard=guard)
    steps = _steps(receipts, restore)
    completed = []
    while progress:
        receipt = steps[progress - 1]
        try:
            _write_step(store, receipt, restore=not restore, guard=guard)
        except Exception:
            # A failing inverse may already have written its desired value.
            guard()
            lower, _, _ = record_progress(
                store, receipts, restore=restore, limit=progress - 1, guard=guard
            )
            progress = lower
        else:
            progress -= 1
        completed.append(receipt)
    record_progress(store, receipts, restore=restore, limit=0, guard=guard)
    state.write(base)
    return completed


def commit_records(
    owner: Any, state: ChangesetState, base: dict[str, Any], *, restore: bool
) -> dict[str, Any] | None:
    """Return None for the unchanged noop-only or Comic facade paths."""
    if owner._has_comic_changeset_operations(base.get("operations")):
        return None
    if restore:
        raw = base.get("appliedRecords")
        if not isinstance(raw, list) or not any(
            isinstance(row, Mapping) and row.get("recordType") in ("decision", "bible_entry")
            for row in raw
        ):
            return None
        prepare_record_rollback(owner._production_store, raw)
        receipts = checked_receipts(raw)
    else:
        operations = base.get("operations")
        if not isinstance(operations, list) or not any(
            isinstance(row, Mapping)
            and isinstance(row.get("kind") or row.get("op"), str)
            and (row.get("kind") or row.get("op")) in _KINDS
            for row in operations
        ):
            return None
        receipts = prepare_record_operations(owner._production_store, operations)
    store = owner._production_store
    record_progress(store, receipts, restore=restore, limit=0)
    journal = _journal(base, receipts, restore)
    state.write(_with_journal(base, journal))
    attempted = 0
    try:
        for index, receipt in enumerate(_steps(receipts, restore), 1):
            attempted = index
            _write_step(store, receipt, restore=restore, guard=state.ensure_current)
        _, endpoint, total = record_progress(
            store, receipts, restore=restore, guard=state.ensure_current
        )
        if endpoint != total:
            _refuse("record_endpoint_unconfirmed")
        _publish_finished(state, base, receipts, restore, journal)
    except Exception:
        try:
            _compensate(state, store, base, receipts, restore, attempted)
        except Exception:
            _refuse("record_recovery_pending", pending=True)
        _refuse("record_commit_failed", pending=True)
    # Finished metadata has been confirmed: cleanup failure never undoes data.
    result = _cleanup(state)
    return _reply(result, _steps(receipts, True) if restore else receipts, restore)


def _check_apply_receipts(base: Any, receipts: Any) -> None:
    operations = base.get("operations")
    if not isinstance(operations, list) or len(operations) != len(receipts):
        _refuse("invalid_record_journal_operations")
    for operation, receipt in zip(operations, receipts, strict=True):
        if not isinstance(operation, Mapping):
            _refuse("invalid_record_journal_operations")
        kind = operation.get("kind") or operation.get("op")
        if kind != receipt["kind"]:
            _refuse("invalid_record_journal_operations")
        if kind == "noop":
            continue
        payload = operation.get("arguments")
        payload = payload if isinstance(payload, Mapping) else operation
        try:
            normalized = (
                pct.create_decision_record(payload)
                if kind == "mark_artifact_decision"
                else pct.create_bible_entry(payload)
            )
        except (TypeError, ValueError, OverflowError):
            _refuse("invalid_record_journal_operations")
        for field in ("createdAt", "updatedAt"):
            if field in normalized:
                stamp = receipt["after"].get(field)
                if not isinstance(stamp, str) or not stamp:
                    _refuse("invalid_record_journal_timestamp")
                normalized[field] = stamp
        if not _same(pct.scrub_for_pct(normalized), receipt["after"]):
            _refuse("invalid_record_journal_operations")


def _parse(record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    journal = record[JOURNAL_KEY]
    fields = {
        "namespace",
        "journalVersion",
        "direction",
        "phase",
        "beforeChangeset",
        "recordReceipts",
        "completedAt",
    }
    if (
        not isinstance(journal, dict)
        or set(journal) != fields
        or journal["namespace"] != _NAMESPACE
        or type(journal["journalVersion"]) is not int
        or journal["journalVersion"] != 1
        or not isinstance(journal["direction"], str)
        or journal["direction"] not in {"apply", "rollback"}
        or not isinstance(journal["phase"], str)
        or journal["phase"] not in {"working", "finished"}
    ):
        _refuse("unsupported_record_journal")
    base = journal["beforeChangeset"]
    if (
        not isinstance(base, dict)
        or JOURNAL_KEY in base
        or base.get("changesetId") != record["changesetId"]
    ):
        _refuse("invalid_record_journal_before")
    receipts = checked_receipts(journal["recordReceipts"])
    restore = journal["direction"] == "rollback"
    if restore:
        if base.get("status") != "applied" or not _same(base.get("appliedRecords"), receipts):
            _refuse("invalid_record_journal_before")
    else:
        _check_apply_receipts(base, receipts)
    stamp = journal["completedAt"]
    if journal["phase"] == "finished":
        if not isinstance(stamp, str) or not stamp:
            _refuse("invalid_record_journal_timestamp")
        expected = _finished(base, receipts, restore, stamp)
    else:
        if stamp is not None:
            _refuse("invalid_record_journal_timestamp")
        expected = base
    if not _same(record, _with_journal(expected, journal)):
        _refuse("record_journal_metadata_changed")
    return base, receipts, restore


async def recover_records(owner: Any, state: ChangesetState) -> dict[str, Any] | None:
    """Inspect pending metadata before the facade's ordinary status gate."""
    record = state.snapshot()
    if JOURNAL_KEY not in record:
        return None
    base, receipts, restore = _parse(record)
    if owner._has_comic_changeset_operations(base.get("operations")):
        _refuse("unsupported_record_journal_operations")
    await owner._require_pct_confirmation(
        "loomstroke_rollback_changeset", state.snapshot(), hard=True
    )
    state.ensure_current()
    store = owner._production_store
    journal = record[JOURNAL_KEY]
    minimum, endpoint, total = record_progress(
        store, receipts, restore=restore, guard=state.ensure_current
    )
    if journal["phase"] == "finished":
        if endpoint != total:
            _refuse("record_finished_endpoint_mismatch")
        flat = state.snapshot()
        flat.pop(JOURNAL_KEY)
        if restore:
            return _reply(_cleanup(state), [], True)
        # A committed apply is cancelled by a flat, ordinary rollback attempt.
        return commit_records(owner, state, flat, restore=True)
    try:
        if not restore:
            completed = _compensate(state, store, base, receipts, False, minimum)
            return _reply(state.snapshot(), completed, True)
        completed = []
        for receipt in _steps(receipts, True)[endpoint:]:
            _write_step(store, receipt, restore=True, guard=state.ensure_current)
            completed.append(receipt)
        _, confirmed, total = record_progress(
            store, receipts, restore=True, guard=state.ensure_current
        )
        if confirmed != total:
            _refuse("record_endpoint_unconfirmed")
        _publish_finished(state, base, receipts, True, journal)
    except Exception:
        _refuse("record_recovery_pending", pending=True)
    return _reply(_cleanup(state), completed, True)
