"""Outcome of planning Relink / Replace for one resource (CT-18 §5.2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .resource_locator import ResourceLocator
from .resource_record import ResourceRecord

__all__ = ["RelinkPlan"]


@dataclass(frozen=True, slots=True)
class RelinkPlan:
    record: ResourceRecord
    source_path: Path
    new_locator: ResourceLocator
    new_hash: str
    byte_size: int
    warnings: tuple[str, ...] = ()

    @property
    def hash_matches(self) -> bool:
        """Same bytes as the record remembers: a pure relocation, not a replacement."""
        return bool(self.new_hash) and self.new_hash == self.record.content_hash

    @property
    def is_readable(self) -> bool:
        return "read_failed" not in self.warnings and "resource_symlink" not in self.warnings

    def updated_record(self, *, width: int | None = None, height: int | None = None):
        return self.record.with_content(
            locator=self.new_locator,
            content_hash=self.new_hash,
            byte_size=self.byte_size,
            width=width,
            height=height,
        )
