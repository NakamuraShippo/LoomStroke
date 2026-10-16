"""One resource's fate inside a package plan (copy / external / embed / missing)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .resource_status import ResourceState

__all__ = ["PackageAction", "PackageAssetEntry"]

PackageAction = Literal["copy", "external", "embed", "missing"]


@dataclass(frozen=True, slots=True)
class PackageAssetEntry:
    resource_id: str
    display_name: str
    action: PackageAction
    state: ResourceState
    package_path: str | None = None
    source_path: Path | None = None
    content_hash: str = ""
    byte_size: int = 0
    media_type: str = "application/octet-stream"
    width: int = 0
    height: int = 0
    reason_code: str = ""

    @property
    def is_copy(self) -> bool:
        return (
            self.action == "copy" and self.package_path is not None and self.source_path is not None
        )

    @property
    def is_omitted(self) -> bool:
        return self.action in {"external", "missing"}

    def with_action(self, action: PackageAction, package_path: str | None) -> PackageAssetEntry:
        return replace(self, action=action, package_path=package_path)

    def with_copied(self, content_hash: str, byte_size: int) -> PackageAssetEntry:
        return replace(self, content_hash=content_hash, byte_size=byte_size)
