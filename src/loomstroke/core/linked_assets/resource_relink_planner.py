"""Plans pointing an existing resource at another file; never relinks on basename alone."""

from __future__ import annotations

from pathlib import Path

from .media_type_table import MediaTypeTable
from .relink_plan import RelinkPlan
from .resource_content_hasher import ResourceContentHasher
from .resource_locator_resolver import ResourceLocatorResolver
from .resource_record import ResourceRecord

__all__ = ["ResourceRelinkPlanner"]


class ResourceRelinkPlanner:
    def __init__(
        self, resolver: ResourceLocatorResolver, hasher: ResourceContentHasher | None = None
    ) -> None:
        self._resolver = resolver
        self._hasher = hasher or ResourceContentHasher()

    def plan(self, record: ResourceRecord, path: Path | str) -> RelinkPlan:
        source = Path(path)
        warnings: list[str] = []
        if not MediaTypeTable.is_decodable(MediaTypeTable.for_path(source)):
            warnings.append("media_type_unsupported")
        if self._resolver.is_symlink(source):
            warnings.append("resource_symlink")
        byte_size = 0
        new_hash = ""
        try:
            byte_size = source.stat().st_size
            new_hash = self._hasher.hash_file(source)
        except OSError:
            warnings.append("read_failed")
        locator = self._resolver.locator_for(source)
        if not locator.is_portable:
            warnings.append("locator_not_portable")
        if new_hash and record.content_hash and new_hash != record.content_hash:
            warnings.append("content_differs")
        return RelinkPlan(record, source, locator, new_hash, byte_size, tuple(warnings))
