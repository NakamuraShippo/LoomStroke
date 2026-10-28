"""Prepare and confirm existing-format shape-index files without UI or rendering."""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomstroke.io.atomic_export_writer import _file_identity, write_atomically

from .shape_record import ShapeRecord

logger = logging.getLogger(__name__)
_FileState = tuple[int, int, int, int]


def parse_shape_rows(entries: Iterable[Any]) -> tuple[list[ShapeRecord], list[str | None]]:
    """Retain the existing row coercions, skip policy and duplicate load order."""
    records: list[ShapeRecord] = []
    slots: list[str | None] = []
    for entry in entries:
        shape_id: str | None = None
        try:
            record = ShapeRecord.from_dict(entry)
            if record.shape_id:
                records.append(record)
                shape_id = record.shape_id
        except Exception:
            logger.debug("Skipping invalid shape record", exc_info=True)
        slots.append(shape_id)
    return records, slots


def _record_meaning(records: Sequence[ShapeRecord]) -> str:
    return json.dumps([record.to_dict() for record in records], ensure_ascii=False, sort_keys=True)


def _file_state(path: Path) -> _FileState:
    value = path.lstat()
    device, inode = _file_identity(value)
    return device, inode, value.st_size, value.st_mtime_ns


def _require_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise FileExistsError("Shape index appeared after preparation")


def _check_file(path: Path, identity: _FileState, raw: bytes) -> None:
    if _file_state(path) != identity or path.read_bytes() != raw or _file_state(path) != identity:
        raise OSError("Shape index changed after preparation")


@dataclass(frozen=True)
class _IndexSource:
    path: Path
    identity: _FileState | None
    raw: bytes
    payload: dict[str, Any]
    slots: list[str | None]

    def confirm(self) -> None:
        if self.identity is None:
            _require_absent(self.path)
        else:
            _check_file(self.path, self.identity, self.raw)


def _read_source(path: Path, current: Sequence[ShapeRecord]) -> _IndexSource:
    try:
        identity = _file_state(path)
    except FileNotFoundError:
        if current:
            raise OSError("An existing shape index is no longer available") from None
        return _IndexSource(path, None, b"", {"version": 1, "records": []}, [])
    raw = path.read_bytes()
    if _file_state(path) != identity or len(raw) != identity[2]:
        raise OSError("Shape index changed while it was read")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records", []), list):
        raise ValueError("Shape index must contain an object with a record list")
    records, slots = parse_shape_rows(payload.get("records", []))
    if _record_meaning(records) != _record_meaning(current):
        raise OSError("Shape index no longer matches the current library")
    return _IndexSource(path, identity, raw, payload, slots)


def _candidate_payload(source: _IndexSource, records: Sequence[ShapeRecord]) -> dict[str, Any]:
    # Match duplicate occurrences in order so distinct unknown metadata remains
    # attached to each retained row. replace_all still chooses its first input.
    originals: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for entry, shape_id in zip(source.payload.get("records", []), source.slots, strict=True):
        if shape_id is not None:
            originals[shape_id].append(entry)
    prepared: deque[dict[str, Any]] = deque()
    for record in records:
        old = originals[record.shape_id].popleft() if originals[record.shape_id] else {}
        prepared.append({**deepcopy(old), **record.to_dict()})
    rows: list[Any] = []
    for entry, shape_id in zip(source.payload.get("records", []), source.slots, strict=True):
        if shape_id is None:
            rows.append(deepcopy(entry))
        elif prepared:
            rows.append(prepared.popleft())
    rows.extend(prepared)
    payload = deepcopy(source.payload)
    payload.update(version=1, records=rows)
    return payload


def _confirmed_destination(path: Path, stage: Path, identity: _FileState, expected: bytes) -> bool:
    try:
        try:
            stage.lstat()
        except FileNotFoundError:
            pass
        else:
            return False
        _check_file(path, identity, expected)
        return True
    except OSError:
        return False


def commit_shape_index(
    path: Path, current: Sequence[ShapeRecord], candidate: Sequence[ShapeRecord]
) -> None:
    """Validate a detached candidate before the caller publishes its memory.

    Unknown root/row keys and skipped raw rows survive ordinary mutations.
    Existing ids, paths and coercions remain the model's responsibility. Final
    check/rename pairs do not exclude another process or guarantee crash safety.
    """
    source = _read_source(path, current)
    text = json.dumps(_candidate_payload(source, candidate), ensure_ascii=False, indent=2)
    wire = text.replace("\n", os.linesep).encode("utf-8")
    expected_meaning = _record_meaning(candidate)
    identity: _FileState | None = None
    stage: Path | None = None

    def encode(temporary: Path) -> None:
        nonlocal identity, stage
        with temporary.open("w", encoding="utf-8") as stream:
            if stream.write(text) != len(text):
                raise OSError("Shape index candidate write was incomplete")
            stream.flush()
            os.fsync(stream.fileno())
        actual = temporary.read_bytes()
        if actual != wire:
            raise OSError("Shape index candidate differs from its prepared content")
        decoded = json.loads(actual.decode("utf-8"))
        parsed, _slots = parse_shape_rows(decoded.get("records", []))
        if _record_meaning(parsed) != expected_meaning:
            raise ValueError("Shape index candidate cannot restore the prepared records")
        identity, stage = _file_state(temporary), temporary
        source.confirm()

    try:
        write_atomically(path, encode)
    except Exception:
        if not (
            identity is not None
            and stage is not None
            and _confirmed_destination(path, stage, identity, wire)
        ):
            raise
