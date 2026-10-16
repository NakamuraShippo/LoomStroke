"""What ``File > Package Project...`` will write (CT-18 §5.3, directory package)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .package_asset_entry import PackageAssetEntry

__all__ = ["PackagePlan"]


@dataclass(frozen=True, slots=True)
class PackagePlan:
    destination: Path
    document_source: Path | None
    document_package_name: str
    entries: tuple[PackageAssetEntry, ...] = ()
    issues: tuple[str, ...] = ()
    font_families: tuple[str, ...] | None = None

    def copies(self) -> tuple[PackageAssetEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_copy)

    def omitted(self) -> tuple[PackageAssetEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_omitted)

    @property
    def can_write(self) -> bool:
        return self.document_source is not None and "document_not_saved" not in self.issues

    def counts(self) -> dict[str, int]:
        counts = {"copy": 0, "external": 0, "embed": 0, "missing": 0}
        for entry in self.entries:
            counts[entry.action] = counts.get(entry.action, 0) + 1
        return counts
