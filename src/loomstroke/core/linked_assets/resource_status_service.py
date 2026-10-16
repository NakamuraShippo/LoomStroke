"""Resolves a record to a file and classifies it (ok / modified / missing / ...)."""

from __future__ import annotations

from pathlib import Path

from .media_type_table import MediaTypeTable
from .resource_content_hasher import ResourceContentHasher
from .resource_locator_resolver import ResourceLocatorResolver
from .resource_record import ResourceRecord
from .resource_status import ResourceStatus

__all__ = ["ResourceStatusService"]


class ResourceStatusService:
    def __init__(
        self, resolver: ResourceLocatorResolver, hasher: ResourceContentHasher | None = None
    ) -> None:
        self._resolver = resolver
        self._hasher = hasher or ResourceContentHasher()

    @property
    def resolver(self) -> ResourceLocatorResolver:
        return self._resolver

    def inspect(self, record: ResourceRecord, *, refresh_hash: bool = True) -> ResourceStatus:
        early = self._precheck(record)
        if early is not None:
            return early
        located = self._locate(record)
        if isinstance(located, ResourceStatus):
            return located
        return self._compare(record, located, refresh_hash)

    # -- steps -------------------------------------------------------------------------

    @staticmethod
    def _precheck(record: ResourceRecord) -> ResourceStatus | None:
        if record.mode == "embedded" or record.locator is None:
            return ResourceStatus(record.resource_id, "ok", reason_code="embedded")
        if not MediaTypeTable.is_decodable(record.media_type):
            return ResourceStatus(
                record.resource_id, "unsupported", reason_code="media_type_unsupported"
            )
        return None

    def _locate(self, record: ResourceRecord) -> ResourceStatus | Path:
        rid = record.resource_id
        assert record.locator is not None
        path = self._resolver.resolve(record.locator)
        if path is None:
            reason = "document_dir_unknown" if record.locator.is_portable else "locator_invalid"
            return ResourceStatus(rid, "missing", reason_code=reason)
        if record.locator.base == "absolute" and path.anchor and not Path(path.anchor).exists():
            return ResourceStatus(rid, "offline", path, reason_code="volume_unavailable")
        if self._resolver.is_symlink(path):
            return ResourceStatus(rid, "unsafe", path, reason_code="resource_symlink")
        if not path.is_file():
            return ResourceStatus(rid, "missing", path, reason_code="file_not_found")
        return path

    def _compare(self, record: ResourceRecord, path: Path, refresh_hash: bool) -> ResourceStatus:
        rid = record.resource_id
        try:
            stat = path.stat()
        except OSError:
            return ResourceStatus(rid, "missing", path, reason_code="stat_failed")
        if not refresh_hash:
            state = "ok" if stat.st_size == record.byte_size else "modified"
            reason = "" if state == "ok" else "size_changed"
            return ResourceStatus(rid, state, path, "", stat.st_size, stat.st_mtime, reason)
        try:
            current = self._hasher.hash_file(path)
        except OSError:
            return ResourceStatus(rid, "missing", path, reason_code="read_failed")
        if self._hasher.matches(record.content_hash, current):
            return ResourceStatus(rid, "ok", path, current, stat.st_size, stat.st_mtime)
        return ResourceStatus(
            rid, "modified", path, current, stat.st_size, stat.st_mtime, "content_changed"
        )
