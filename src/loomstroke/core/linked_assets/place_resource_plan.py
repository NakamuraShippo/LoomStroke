"""Outcome of planning ``File > Place``: the record to attach plus warnings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .media_type_table import MediaTypeTable
from .resource_record import ResourceRecord

__all__ = ["PlaceResourcePlan"]


@dataclass(frozen=True, slots=True)
class PlaceResourcePlan:
    source_path: Path
    record: ResourceRecord
    warnings: tuple[str, ...] = ()

    @property
    def is_supported(self) -> bool:
        return self.record.kind == "raster_image" and MediaTypeTable.is_decodable(
            self.record.media_type
        )

    @property
    def is_safe(self) -> bool:
        return "resource_symlink" not in self.warnings
