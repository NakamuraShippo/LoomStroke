"""Checked ownership of one PCT changeset across confirmation and metadata I/O.

This boundary confirms a metadata write; it does not compensate record writes
or failed metadata writes. Record-only transaction recovery is a separate unit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, NoReturn, cast

from . import production as pct
from .types import McpStructuredError, ReasonCode


def _refuse(reason: str, *, failed: bool = False) -> NoReturn:
    raise McpStructuredError(
        ReasonCode.BACKEND_FAILED if failed else ReasonCode.CONFLICT,
        "Production changeset metadata cannot be changed safely.",
        details={"reason": reason},
    ) from None


def _encoded(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        _refuse("invalid_changeset_json")


def _detached(value: Any) -> Any:
    encoded = _encoded(value)
    if encoded != _encoded(pct.scrub_for_pct(value)):
        _refuse("changeset_requires_redaction")
    return json.loads(encoded)


def _rows(store: Any) -> list[dict[str, Any]]:
    try:
        raw = store._read_records_checked("changesets")
    except Exception:
        _refuse("changeset_read_failed")
    rows = _detached(raw)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        _refuse("invalid_changeset_collection")
    return cast(list[dict[str, Any]], rows)


def _find(rows: list[dict[str, Any]], identifier: str) -> dict[str, Any] | None:
    matches = [row for row in rows if str(row.get("changesetId") or "") == identifier]
    if len(matches) > 1:
        _refuse("ambiguous_changeset_identity")
    if not matches:
        return None
    record = matches[0]
    if record.get("changesetId") != identifier or (
        "schemaVersion" in record and record["schemaVersion"] != "pct.changeset.v1"
    ):
        _refuse("invalid_changeset_identity")
    return record


class ChangesetState:
    """Short-lived ownership of detached metadata; never stored on the service."""

    def __init__(
        self,
        owner: Any,
        store: Any,
        root: Any,
        record: dict[str, Any] | None,
        *,
        identifier: str | None = None,
    ) -> None:
        self._owner = owner
        self._store = store
        self._root = root
        self._before = record
        self._identifier = (
            str(record["changesetId"]) if record is not None else str(identifier or "")
        )

    @classmethod
    def capture(cls, owner: Any, identifier: str) -> ChangesetState | None:
        state = cls._capture_for_creation(owner, identifier)
        return state if state._before is not None else None

    @classmethod
    def _capture_for_creation(cls, owner: Any, identifier: str) -> ChangesetState:
        """Keep a checked absence as an owned expectation across confirmation."""
        store = owner._production_store
        root = getattr(store, "root", None)
        rows = _rows(store)
        if owner._production_store is not store or getattr(store, "root", None) != root:
            _refuse("changeset_store_changed")
        record = _find(rows, identifier)
        return cls(owner, store, root, record, identifier=identifier)

    def _existing_snapshot(self) -> dict[str, Any] | None:
        return self.snapshot() if self._before is not None else None

    def snapshot(self) -> dict[str, Any]:
        if self._before is None:
            _refuse("changeset_not_found")
        return cast(dict[str, Any], _detached(self._before))

    def _check_store(self) -> None:
        if (
            self._owner._production_store is not self._store
            or getattr(self._store, "root", None) != self._root
        ):
            _refuse("changeset_store_changed")

    def _current_rows(self) -> list[dict[str, Any]]:
        self._check_store()
        rows = _rows(self._store)
        self._check_store()
        if _encoded(_find(rows, self._identifier)) != _encoded(self._before):
            _refuse("changeset_changed_since_confirmation")
        return rows

    def ensure_current(self) -> None:
        """Reject a changed store or record before any operations are executed."""
        self._current_rows()

    def write(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        """Preserve fresh other IDs and confirm the result even after an exception."""
        desired = cast(dict[str, Any], _detached(candidate))
        _find([desired], self._identifier)
        if desired.get("changesetId") != self._identifier:
            _refuse("invalid_changeset_identity")
        rows = self._current_rows()
        updated = [row for row in rows if row.get("changesetId") != self._identifier]
        updated.append(desired)
        updated.sort(key=lambda row: str(row.get("changesetId") or ""))
        write_failed = False
        try:
            self._store.write_records("changesets", _detached(updated))
        except Exception:
            write_failed = True
        try:
            self._check_store()
            observed = _find(_rows(self._store), self._identifier)
            self._check_store()
        except Exception:
            _refuse("changeset_write_unconfirmed", failed=True)
        if _encoded(observed) == _encoded(desired):
            self._before = cast(dict[str, Any], _detached(desired))
            return desired
        reason = (
            "changeset_write_failed"
            if write_failed and _encoded(observed) == _encoded(self._before)
            else "changeset_write_unconfirmed"
        )
        _refuse(reason, failed=True)
