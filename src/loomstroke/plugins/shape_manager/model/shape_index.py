from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .shape_index_io import commit_shape_index, parse_shape_rows
from .shape_record import ShapeRecord

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _require_no_load_failure(index: ShapeIndex) -> None:
    if index._load_failure is not None:
        raise OSError("The shape index must be reloaded successfully before saving")


class ShapeIndex:
    def __init__(self, index_path: Path) -> None:
        self._index_path = index_path
        self._records: list[ShapeRecord] = []
        self._by_id: dict[str, ShapeRecord] = {}
        self._source_seen = False
        self._load_failure: str | None = None

    @property
    def path(self) -> Path:
        return self._index_path

    def load(self) -> None:
        try:
            self._index_path.stat()
        except FileNotFoundError:
            if self._source_seen or self._load_failure is not None:
                # An external deletion does not authorize resetting a known
                # library or clearing an earlier failed refresh.
                self._load_failure = self._load_failure or "missing"
                logger.warning("An existing shape index is no longer available")
            return
        except OSError as exc:
            self._load_failure = "read"
            logger.warning("Failed to read shape index: %s", exc)
            return
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._load_failure = "read" if isinstance(exc, OSError) else "json"
            logger.warning("Failed to read shape index: %s", exc)
            return
        try:
            records, _slots = parse_shape_rows(payload.get("records", []))
            by_id = {record.shape_id: record for record in records}
        except Exception:
            self._load_failure = "prepare"
            raise
        self._records, self._by_id = records, by_id
        self._source_seen = True
        self._load_failure = None

    def save(self) -> None:
        self._commit(self._records)

    def _commit(self, records: Iterable[ShapeRecord]) -> None:
        _require_no_load_failure(self)
        candidate = list(records)
        by_id = {record.shape_id: record for record in candidate}
        commit_shape_index(self._index_path, self._records, candidate)
        self._records, self._by_id = candidate, by_id
        self._source_seen = True

    def all(self) -> list[ShapeRecord]:
        return list(self._records)

    def get(self, shape_id: str) -> ShapeRecord | None:
        return self._by_id.get(shape_id)

    def add(self, record: ShapeRecord) -> None:
        if record.shape_id in self._by_id:
            raise ValueError(f"Duplicate shape id: {record.shape_id}")
        self._commit([*self._records, record])

    def update(self, record: ShapeRecord) -> None:
        if record.shape_id not in self._by_id:
            raise ValueError(f"Missing shape id: {record.shape_id}")
        self._commit([record if r.shape_id == record.shape_id else r for r in self._records])

    def remove(self, shape_id: str) -> None:
        if shape_id not in self._by_id:
            return
        self._commit([record for record in self._records if record.shape_id != shape_id])

    def rename(self, shape_id: str, new_name: str) -> ShapeRecord:
        record = self._by_id.get(shape_id)
        if record is None:
            raise ValueError(f"Missing shape id: {shape_id}")
        updated = replace(record, name=new_name, updated_at_iso=_now_iso())
        self.update(updated)
        return updated

    def replace_all(self, records: Iterable[ShapeRecord]) -> None:
        unique: list[ShapeRecord] = []
        seen: set[str] = set()
        for record in records:
            if record.shape_id in seen:
                continue
            seen.add(record.shape_id)
            unique.append(record)
        self._commit(unique)


_DELETE_SHAPE_INDEX_DEFAULTS = tuple(
    (name, ShapeIndex.__dict__[name]) for name in ("remove", "get", "all", "path")
)
