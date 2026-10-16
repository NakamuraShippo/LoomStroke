from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .shape_record import ShapeRecord

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ShapeIndex:
    def __init__(self, index_path: Path) -> None:
        self._index_path = index_path
        self._records: list[ShapeRecord] = []
        self._by_id: dict[str, ShapeRecord] = {}

    @property
    def path(self) -> Path:
        return self._index_path

    def load(self) -> None:
        if not self._index_path.exists():
            self._records = []
            self._by_id = {}
            return
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read shape index: %s", exc)
            self._records = []
            self._by_id = {}
            return
        records = []
        for entry in payload.get("records", []):
            try:
                record = ShapeRecord.from_dict(entry)
                if record.shape_id:
                    records.append(record)
            except Exception:
                logger.debug("Skipping invalid shape record", exc_info=True)
        self._records = records
        self._by_id = {record.shape_id: record for record in records}

    def save(self) -> None:
        data = {
            "version": 1,
            "records": [record.to_dict() for record in self._records],
        }
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        self._index_path.write_text(payload, encoding="utf-8")

    def all(self) -> list[ShapeRecord]:
        return list(self._records)

    def get(self, shape_id: str) -> ShapeRecord | None:
        return self._by_id.get(shape_id)

    def add(self, record: ShapeRecord) -> None:
        if record.shape_id in self._by_id:
            raise ValueError(f"Duplicate shape id: {record.shape_id}")
        self._records.append(record)
        self._by_id[record.shape_id] = record
        self.save()

    def update(self, record: ShapeRecord) -> None:
        if record.shape_id not in self._by_id:
            raise ValueError(f"Missing shape id: {record.shape_id}")
        self._by_id[record.shape_id] = record
        self._records = [record if r.shape_id == record.shape_id else r for r in self._records]
        self.save()

    def remove(self, shape_id: str) -> None:
        if shape_id not in self._by_id:
            return
        self._records = [record for record in self._records if record.shape_id != shape_id]
        self._by_id.pop(shape_id, None)
        self.save()

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
        self._records = unique
        self._by_id = {record.shape_id: record for record in unique}
        self.save()
