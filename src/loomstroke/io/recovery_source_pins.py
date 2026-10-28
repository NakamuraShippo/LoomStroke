"""Runtime retention of recovery sources that may still back document pages."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .atomic_export_writer import write_atomically

logger = logging.getLogger(__name__)


def _path_key(value: Any) -> Path | None:
    if not isinstance(value, (str, Path)) or not value:
        return None
    try:
        return Path(value).resolve()
    except (OSError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class RecoveryLoadContext:
    source: Path
    original_path: Path | None


class RecoverySourcePins:
    """Keep attempted sources until a later, explicit unused-source proof."""

    def __init__(self) -> None:
        self._sources: set[Path] = set()
        self._loaded_owners: dict[Path, Any] = {}
        self._source_owners: dict[Path, Any] = {}
        self._shared_sources: set[Path] = set()
        self._loading: list[RecoveryLoadContext] = []

    @property
    def current(self) -> RecoveryLoadContext | None:
        return self._loading[-1] if self._loading else None

    @contextmanager
    def loading(
        self, source: Path, original_path: Path | None, *, owner: Any = None
    ) -> Iterator[RecoveryLoadContext]:
        key = _path_key(source)
        if key is None:
            raise ValueError("Invalid recovery source path")
        context = RecoveryLoadContext(key, original_path)
        self._sources.add(key)
        if key not in self._source_owners:
            self._source_owners[key] = owner
        elif self._source_owners[key] is not owner:
            # A current owner's proof says nothing about another service
            # that may still hold a lazy loader, including a partial load.
            self._shared_sources.add(key)
        # A failed retry must not inherit an earlier successful load's
        # eligibility for release. Partial loads remain conservative pins.
        self._loaded_owners.pop(key, None)
        self._loading.append(context)
        try:
            yield context
        finally:
            self._loading.pop()

    def is_protected(self, source: Any) -> bool:
        return _path_key(source) in self._sources

    def mark_loaded(self, source: Path, *, owner: Any) -> None:
        """Allow later proof-based release after a fully confirmed recovery."""
        key = _path_key(source)
        if key in self._sources:
            self._loaded_owners[key] = owner

    def release_unused(self, sources: Any, *, owner: Any) -> set[Path]:
        """Release only confirmed loads with a trustworthy, owner-matched proof."""
        if self._loading or not isinstance(sources, (set, frozenset)):
            return set()
        used = {_path_key(source) for source in sources}
        if None in used:
            return set()
        released = {
            source
            for source, loaded_owner in self._loaded_owners.items()
            if loaded_owner is owner and source not in used and source not in self._shared_sources
        }
        self._sources.difference_update(released)
        for source in released:
            del self._loaded_owners[source]
            self._source_owners.pop(source, None)
        return released

    def retain_newest(self, records: list[Any], limit: int) -> list[Any]:
        if limit <= 0:
            return records
        unprotected = [index for index, row in enumerate(records) if not self._protected_row(row)]
        keep = set(unprotected[-limit:])
        return [
            row for index, row in enumerate(records) if index in keep or self._protected_row(row)
        ]

    def _protected_row(self, row: Any) -> bool:
        return isinstance(row, dict) and self.is_protected(row.get("file_path"))


def release_proven_unused_sources(manager: Any) -> None:
    """Ask the actual loader owner; missing/custom capabilities keep all pins."""
    pins = getattr(manager, "_recovery_source_pins", None)
    owner = getattr(manager, "_document_service", None)
    if not isinstance(pins, RecoverySourcePins) or pins.current is not None:
        return
    query = getattr(owner, "_recovery_sources_in_use", None)
    if not callable(query):
        return
    try:
        pins.release_unused(query(), owner=owner)
    except Exception:
        logger.warning(
            "Cannot prove recovery sources unused; keeping their protection", exc_info=True
        )


def preserve_cleanup_metadata(recovery_file: Path, deletions: Sequence[Path]) -> None:
    """Confirm a safe metadata rewrite before deleting any old archive."""
    if not recovery_file.exists() or not deletions:
        return
    records = json.loads(recovery_file.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("Invalid recovery metadata list")
    removing = {_path_key(path) for path in deletions}
    kept = [
        row
        for row in records
        if not isinstance(row, dict) or _path_key(row.get("file_path")) not in removing
    ]
    if kept != records:
        payload = json.dumps(kept, indent=2)
        write_atomically(
            recovery_file, lambda temporary: temporary.write_text(payload, encoding="utf-8")
        )
