"""Everything a format writer needs, frozen (CT-26 spec §データモデル)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .publication_item import PublicationItem
from .publication_metadata import PublicationMetadata

__all__ = ["DigitalComicPackageRequest", "PackageFormat"]

PackageFormat = Literal["cbz", "epub3_fxl", "image_manifest_v1"]


@dataclass(frozen=True, slots=True)
class DigitalComicPackageRequest:
    format_id: PackageFormat
    binding: Literal["right", "left"]
    items: tuple[PublicationItem, ...]
    metadata: PublicationMetadata
    destination: Path
    include_loomstroke_manifest: bool = True
    page_labels: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.format_id not in ("cbz", "epub3_fxl", "image_manifest_v1"):
            raise ValueError(f"unsupported package format: {self.format_id}")
        if self.binding not in ("right", "left"):
            raise ValueError(f"unsupported binding: {self.binding}")
        if self.page_labels and len(self.page_labels) != len(self.items):
            raise ValueError("page_labels must match items")

    @property
    def cover(self) -> PublicationItem | None:
        for item in self.items:
            if item.role == "cover":
                return item
        return None

    @property
    def progression(self) -> str:
        return "rtl" if self.binding == "right" else "ltr"
