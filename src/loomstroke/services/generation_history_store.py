"""Persistent generation history store.

Manages ``GenerationRecord`` persistence under ``user_data/generation_history/``
with atomic writes, automatic pruning, and provider dispatch.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from loomstroke.core.generation_record import GenerationRecord
from loomstroke.io.atomic_export_writer import write_atomically

if TYPE_CHECKING:
    from loomstroke.plugins.sdk.generation_provider import GenerationProvider

logger = logging.getLogger(__name__)

# Default storage root relative to the working directory.
_DEFAULT_BASE = Path("user_data") / "generation_history"
_INDEX_FILE = "index.json"
_IMAGES_DIR = "images"
_THUMBNAILS_DIR = "thumbnails"
_DEFAULT_MAX_RECORDS = 500


class GenerationHistoryStore(QObject):
    """Persistent store for AI generation history records.

    Signals:
        record_added(str):   Emitted with job_id after a record is added.
        record_updated(str): Emitted with job_id after a record is updated.
        record_removed(str): Emitted with job_id after a record is removed.
        records_loaded():    Emitted after records are loaded from disk.
    """

    record_added = Signal(str)
    record_updated = Signal(str)
    record_removed = Signal(str)
    records_loaded = Signal()

    def __init__(
        self,
        base_path: str | Path | None = None,
        max_records: int = _DEFAULT_MAX_RECORDS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._base = Path(base_path) if base_path else _DEFAULT_BASE
        self._max_records = max_records
        self._records: list[GenerationRecord] = []
        self._providers: dict[str, GenerationProvider] = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)
        self._save_timer.timeout.connect(self.save)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        (self._base / _IMAGES_DIR).mkdir(exist_ok=True)
        (self._base / _THUMBNAILS_DIR).mkdir(exist_ok=True)

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a relative path stored in a record to an absolute path."""
        return self._base / relative_path

    # ------------------------------------------------------------------
    # Load / Save (atomic)
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load records from ``index.json``."""
        self._ensure_dirs()
        index_path = self._base / _INDEX_FILE
        if not index_path.exists():
            self._records = []
            self.records_loaded.emit()
            return

        try:
            raw = index_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, list):
                logger.warning("index.json is not a list; resetting")
                data = []
            self._records = [GenerationRecord.from_dict(d) for d in data]
        except Exception:
            logger.exception("Failed to load generation history index")
            self._records = []

        self.records_loaded.emit()

    def save(self) -> None:
        """Persist records to ``index.json`` atomically."""
        self._save_with_pruning()

    def _save_with_pruning(self) -> bool:
        limit = max(0, self._max_records)
        return self._commit_records(self._records[:limit], self._records[limit:])

    def _commit_records(
        self, records: list[GenerationRecord], removed: list[GenerationRecord]
    ) -> bool:
        """Publish the replacement index before unlinking its retired assets."""
        index_path = self._base / _INDEX_FILE
        try:
            self._ensure_dirs()
            payload = json.dumps(
                [r.to_dict() for r in records],
                ensure_ascii=False,
                indent=2,
            )
            write_atomically(index_path, lambda tmp: tmp.write_text(payload, encoding="utf-8"))
        except Exception:
            logger.exception("Failed to save generation history index")
            return False
        self._save_timer.stop()
        self._records = records
        if removed:
            self._delete_record_files(removed)
            for record in removed:
                self.record_removed.emit(record.job_id)
        return True

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _schedule_save(self) -> None:
        """Schedule a debounced save (1s after last mutation)."""
        if not self._save_timer.isActive():
            self._save_timer.start()

    def add_record(self, record: GenerationRecord) -> None:
        """Add a record, pruning oldest when over the limit."""
        self._records.insert(0, record)
        if len(self._records) <= self._max_records or not self._save_with_pruning():
            # An index write failure keeps every old record and the new result.
            self._schedule_save()
        self.record_added.emit(record.job_id)

    def remove_record(self, job_id: str) -> bool:
        """Remove a record and its associated image files."""
        for i, rec in enumerate(self._records):
            if rec.job_id == job_id:
                remaining = self._records[:i] + self._records[i + 1 :]
                return self._commit_records(remaining, [rec])
        return False

    def get_record(self, job_id: str) -> GenerationRecord | None:
        """Look up a single record by job_id."""
        for rec in self._records:
            if rec.job_id == job_id:
                return rec
        return None

    def update_record(self, record: GenerationRecord) -> bool:
        """Update an existing record in-place and emit record_updated."""
        for i, rec in enumerate(self._records):
            if rec.job_id == record.job_id:
                self._records[i] = record
                self._schedule_save()
                self.record_updated.emit(record.job_id)
                return True
        return False

    def list_records(
        self,
        service_id: str | None = None,
        status: str | None = None,
    ) -> list[GenerationRecord]:
        """Return records in descending order (newest first), optionally filtered."""
        result = self._records
        if service_id:
            result = [r for r in result if r.service_id == service_id]
        if status:
            result = [r for r in result if r.status == status]
        return list(result)

    # ------------------------------------------------------------------
    # Image persistence
    # ------------------------------------------------------------------

    def save_image(self, job_id: str, image_bytes: bytes) -> str:
        """Save original image (PNG, unmodified) and return its relative path."""
        self._ensure_dirs()
        rel = f"{_IMAGES_DIR}/{job_id}.png"
        full = self._base / rel
        full.write_bytes(image_bytes)
        return rel

    def save_thumbnail(self, job_id: str, thumbnail_bytes: bytes) -> str:
        """Save thumbnail (WebP) and return its relative path."""
        self._ensure_dirs()
        rel = f"{_THUMBNAILS_DIR}/{job_id}.webp"
        full = self._base / rel
        full.write_bytes(thumbnail_bytes)
        return rel

    def _delete_record_files(self, removed: list[GenerationRecord]) -> None:
        """Clean up only unreferenced files in the store's asset directories."""
        retained = {
            Path(rel)
            for record in self._records
            for rel in (record.image_path, record.thumbnail_path)
            if isinstance(rel, str) and rel
        }
        for record in removed:
            for rel, folder, suffix in (
                (record.image_path, _IMAGES_DIR, ".png"),
                (record.thumbnail_path, _THUMBNAILS_DIR, ".webp"),
            ):
                if not isinstance(rel, str) or not rel:
                    continue
                relative = Path(rel)
                if (
                    relative in retained
                    or relative.parent != Path(folder)
                    or relative.suffix.lower() != suffix
                ):
                    continue
                asset = self._base / relative
                try:
                    if (
                        not asset.is_symlink()
                        and asset.resolve().parent == self._base.resolve() / folder
                    ):
                        asset.unlink(missing_ok=True)
                except (OSError, RuntimeError, ValueError):
                    logger.debug(
                        "Failed to remove an unused generation history asset", exc_info=True
                    )

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def register_provider(self, provider: GenerationProvider) -> None:
        """Register a GenerationProvider for a service_id."""
        self._providers[provider.service_id] = provider
        logger.info("Registered generation provider: %s", provider.service_id)

    def unregister_provider(self, service_id: str) -> None:
        """Unregister a GenerationProvider."""
        self._providers.pop(service_id, None)

    def get_provider(self, service_id: str) -> GenerationProvider | None:
        """Return the registered provider for the given service_id, or None."""
        return self._providers.get(service_id)

    def list_provider_ids(self) -> list[str]:
        """Return a list of registered provider service IDs."""
        return list(self._providers.keys())

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def base_path(self) -> Path:
        return self._base
