"""Checked normal apply/rollback for PCT decision and Bible records.

Receipts are additive JSON metadata. This unit does not compensate I/O failures
mid-apply/rollback or a later changeset-status write; those need a separate unit.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, NoReturn, cast

from . import production as pct
from .types import McpStructuredError, ReasonCode

_Key = tuple[str, str]
_ID_FIELDS = {"decision": "decisionId", "bible_entry": "entryId"}
_SCHEMAS = {"decision": "pct.artifact_decision.v1", "bible_entry": "pct.bible_entry.v1"}
_KINDS = {"decision": "mark_artifact_decision", "bible_entry": "add_bible_rule"}


def _refuse(reason: str) -> NoReturn:
    raise McpStructuredError(
        ReasonCode.CONFLICT,
        "Production changeset records cannot be changed safely.",
        details={"reason": reason},
    ) from None


def _encoded(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def _copy(value: Any) -> Any:
    try:
        return json.loads(_encoded(value))
    except (TypeError, ValueError):
        _refuse("invalid_record_json")


def _same(left: Any, right: Any) -> bool:
    return _encoded(left) == _encoded(right)


def _safe(value: Any) -> Any:
    detached = _copy(value)
    if not _same(detached, pct.scrub_for_pct(detached)):
        _refuse("record_requires_redaction")
    return detached


def _collection(store: Any, key: _Key) -> list[dict[str, Any]]:
    try:
        rows = (
            store._read_records_checked("decisions")
            if key[0] == "decision"
            else store._read_bible_entries_checked(key[1])
        )
    except Exception:
        _refuse("record_read_failed")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        _refuse("invalid_record_collection")
    return cast(list[dict[str, Any]], _safe(rows))


def _key(receipt: Mapping[str, Any]) -> tuple[_Key, str]:
    record_type, identifier = receipt.get("recordType"), receipt.get("recordId")
    if (
        not isinstance(record_type, str)
        or record_type not in _ID_FIELDS
        or not isinstance(identifier, str)
        or not identifier
    ):
        _refuse("invalid_record_identity")
    if receipt.get("kind") != _KINDS[record_type]:
        _refuse("invalid_record_kind")
    scope = ""
    if record_type == "bible_entry":
        candidate_scope = receipt.get("scope")
        if not isinstance(candidate_scope, str) or candidate_scope not in pct._BIBLE_SCOPE_FILES:
            _refuse("invalid_record_scope")
        scope = candidate_scope
    return (record_type, scope), identifier


def _record(value: Any, key: _Key, identifier: str) -> dict[str, Any]:
    value = _safe(value)
    if (
        not isinstance(value, dict)
        or value.get(_ID_FIELDS[key[0]]) != identifier
        or ("schemaVersion" in value and value["schemaVersion"] != _SCHEMAS[key[0]])
        or (key[0] == "bible_entry" and value.get("scope") != key[1])
    ):
        _refuse("record_receipt_identity_mismatch")
    return cast(dict[str, Any], value)


def _find(rows: list[dict[str, Any]], key: _Key, identifier: str) -> dict[str, Any] | None:
    field = _ID_FIELDS[key[0]]
    matches = [row for row in rows if str(row.get(field) or "") == identifier]
    if len(matches) > 1:
        _refuse("ambiguous_record_identity")
    return _record(matches[0], key, identifier) if matches else None


def _replace(
    rows: list[dict[str, Any]], key: _Key, identifier: str, value: dict[str, Any] | None
) -> list[dict[str, Any]]:
    field = _ID_FIELDS[key[0]]
    result = [row for row in rows if str(row.get(field) or "") != identifier]
    if value is not None:
        result.append(_copy(value))
    return sorted(result, key=lambda row: str(row.get(field) or ""))


def _write_step(
    store: Any,
    receipt: dict[str, Any],
    *,
    restore: bool,
    guard: Callable[[], None] | None = None,
) -> None:
    if guard is not None:
        guard()
    key, identifier = _key(receipt)
    rows = _collection(store, key)
    if guard is not None:
        guard()
    expected = receipt["after"] if restore else receipt["before"]
    desired = receipt["before"] if restore else receipt["after"]
    if not _same(_find(rows, key, identifier), expected):
        _refuse("record_changed_since_preflight")
    updated = _replace(rows, key, identifier, desired)
    if key[0] == "decision":
        store.write_records("decisions", updated)
    else:
        store._write_bible_entries(key[1], updated)
    if guard is not None:
        guard()
    if not _same(_find(_collection(store, key), key, identifier), desired):
        _refuse("record_write_unconfirmed")
    if guard is not None:
        guard()


def prepare_record_operations(store: Any, operations: Any) -> list[dict[str, Any]]:
    """Prepare detached values before any record is written."""
    states: dict[_Key, list[dict[str, Any]]] = {}
    prepared: list[dict[str, Any]] = []
    for operation in operations if isinstance(operations, list) else []:
        if not isinstance(operation, Mapping):
            continue
        kind = str(operation.get("kind") or operation.get("op") or "")
        if kind == "noop":
            prepared.append({"kind": kind, "recordType": "noop", "recordId": None})
            continue
        payload = operation.get("arguments")
        payload = payload if isinstance(payload, Mapping) else operation
        receipt: dict[str, Any]
        if kind == "mark_artifact_decision":
            after = _copy(pct.scrub_for_pct(pct.create_decision_record(payload)))
            receipt = {"kind": kind, "recordType": "decision", "recordId": after["decisionId"]}
        elif kind == "add_bible_rule":
            after = _copy(pct.scrub_for_pct(pct.create_bible_entry(payload)))
            receipt = {
                "kind": kind,
                "recordType": "bible_entry",
                "recordId": after["entryId"],
                "scope": after["scope"],
            }
        else:
            continue
        key, identifier = _key(receipt)
        if key not in states:
            states[key] = _collection(store, key)
        before = _find(states[key], key, identifier)
        receipt.update(
            undoVersion=1,
            beforePresent=before is not None,
            before=_copy(before),
            after=_record(after, key, identifier),
        )
        prepared.append(receipt)
        states[key] = _replace(states[key], key, identifier, after)
    return prepared


def apply_record_operations(store: Any, operations: Any) -> list[dict[str, Any]]:
    """Preserve the direct helper's normal apply contract."""
    prepared = prepare_record_operations(store, operations)
    for receipt in prepared:
        if receipt["recordType"] != "noop":
            _write_step(store, receipt, restore=False)
    return prepared


def checked_receipts(applied: Any) -> list[dict[str, Any]]:
    """Validate receipt identities and JSON types independently of current values."""
    if not isinstance(applied, list):
        _refuse("missing_record_receipts")
    prepared: list[dict[str, Any]] = []
    for raw in applied:
        if not isinstance(raw, Mapping):
            _refuse("invalid_record_receipt")
        receipt = _safe(raw)
        if receipt.get("recordType") == "noop":
            if (
                receipt.get("kind") != "noop"
                or receipt.get("recordId") is not None
                or any(
                    field in receipt
                    for field in ("undoVersion", "beforePresent", "before", "after")
                )
            ):
                _refuse("invalid_noop_receipt")
            prepared.append(receipt)
            continue
        key, identifier = _key(receipt)
        if type(receipt.get("undoVersion")) is not int or receipt["undoVersion"] != 1:
            _refuse("missing_record_before")
        present = receipt.get("beforePresent")
        if type(present) is not bool or "before" not in receipt or "after" not in receipt:
            _refuse("missing_record_before")
        if present:
            _record(receipt["before"], key, identifier)
        elif receipt["before"] is not None:
            _refuse("invalid_new_record_before")
        _record(receipt["after"], key, identifier)
        prepared.append(receipt)
    return prepared


def prepare_record_rollback(store: Any, applied: Any) -> list[dict[str, Any]]:
    """Reject all inconsistent current values before the first inverse."""
    states: dict[_Key, list[dict[str, Any]]] = {}
    prepared: list[dict[str, Any]] = []
    for receipt in reversed(checked_receipts(applied)):
        if receipt["recordType"] == "noop":
            continue
        key, identifier = _key(receipt)
        if key not in states:
            states[key] = _collection(store, key)
        if not _same(_find(states[key], key, identifier), receipt["after"]):
            _refuse("record_changed_since_apply")
        states[key] = _replace(states[key], key, identifier, receipt["before"])
        prepared.append(receipt)
    return prepared


def rollback_record_operations(store: Any, applied: Any) -> list[dict[str, Any]]:
    """Preserve the direct helper's normal reverse rollback contract."""
    prepared = prepare_record_rollback(store, applied)
    for receipt in prepared:
        _write_step(store, receipt, restore=True)
    return prepared


def _observed_targets(
    store: Any,
    targets: Mapping[tuple[_Key, str], str],
    guard: Callable[[], None] | None,
) -> dict[tuple[_Key, str], str]:
    observed = {target: _encoded(None) for target in targets}
    for key in {target[0] for target in targets}:
        if guard is not None:
            guard()
        wanted = {identifier for collection, identifier in targets if collection == key}
        found: set[str] = set()
        for row in _collection(store, key):
            identifier = str(row.get(_ID_FIELDS[key[0]]) or "")
            if identifier in wanted:
                if identifier in found:
                    _refuse("ambiguous_record_identity")
                found.add(identifier)
                observed[(key, identifier)] = _encoded(_record(row, key, identifier))
        if guard is not None:
            guard()
    return observed


def record_progress(
    store: Any,
    receipts: Any,
    *,
    restore: bool,
    limit: int | None = None,
    guard: Callable[[], None] | None = None,
) -> tuple[int, int, int]:
    """Match one whole-operation prefix in linear work, without prefix snapshots."""
    steps = [row for row in checked_receipts(receipts) if row["recordType"] != "noop"]
    if restore:
        steps.reverse()
    initial: dict[tuple[_Key, str], str] = {}
    current_model: dict[tuple[_Key, str], str] = {}
    transitions: list[tuple[tuple[_Key, str], str]] = []
    for receipt in steps:
        target = _key(receipt)
        expected = _encoded(receipt["after"] if restore else receipt["before"])
        desired = _encoded(receipt["before"] if restore else receipt["after"])
        initial.setdefault(target, expected)
        if current_model.get(target, expected) != expected:
            _refuse("invalid_record_chain")
        current_model[target] = desired
        transitions.append((target, desired))
    observed = _observed_targets(store, initial, guard)
    model = dict(initial)
    mismatches = sum(model[target] != observed[target] for target in initial)
    minimum = maximum = 0 if mismatches == 0 else -1
    for index, (target, desired) in enumerate(transitions, 1):
        mismatches -= model[target] != observed[target]
        model[target] = desired
        mismatches += desired != observed[target]
        if mismatches == 0 and (limit is None or index <= limit):
            if minimum < 0:
                minimum = index
            maximum = index
    if minimum < 0:
        _refuse("record_state_is_not_a_known_prefix")
    return minimum, maximum, len(steps)
