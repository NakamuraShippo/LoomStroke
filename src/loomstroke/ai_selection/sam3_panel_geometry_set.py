"""Ordered panel geometry with source kind, revision and provenance (CT-23 §6 / §9.1)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar

from .sam3_panel_geometry import Sam3PanelGeometry

__all__ = ["Sam3PanelGeometrySet"]


@dataclass(frozen=True, slots=True)
class Sam3PanelGeometrySet:
    source_kind: str
    panels: tuple[Sam3PanelGeometry, ...] = ()
    page_id: str | None = None
    warnings: tuple[str, ...] = ()
    provenance: str = ""
    schema_version: str = "sam3.panel_geometry_set.v1"

    SCHEMA_VERSION: ClassVar[str] = "sam3.panel_geometry_set.v1"

    @classmethod
    def build(
        cls,
        source_kind: str,
        panels: Iterable[Sam3PanelGeometry],
        *,
        page_id: str | None = None,
        provenance: str = "",
    ) -> Sam3PanelGeometrySet:
        ordered = tuple(sorted(panels, key=lambda p: (p.reading_order, p.panel_id)))
        warnings: list[str] = []
        for index, panel in enumerate(ordered):
            if any(panel.overlaps(other) for other in ordered[index + 1 :]):
                warnings.append("panel_geometry_overlap")
                break
        if len({p.panel_id for p in ordered}) != len(ordered):
            warnings.append("panel_geometry_duplicate_id")
        return cls(source_kind, ordered, page_id, tuple(warnings), provenance)

    @property
    def is_empty(self) -> bool:
        return not self.panels

    @property
    def revision(self) -> str:
        digest = hashlib.sha256()
        for panel in self.panels:
            digest.update(f"{panel.panel_id}:{panel.bbox}:{panel.reading_order};".encode())
        return digest.hexdigest()[:16]

    def to_panel_bboxes(self) -> dict[str, tuple[int, int, int, int]]:
        """The ``panel_bboxes`` mapping accepted by the manga mask package builder."""
        return {panel.panel_id: tuple(int(v) for v in panel.bbox) for panel in self.panels}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "sourceKind": self.source_kind,
            "pageId": self.page_id,
            "revision": self.revision,
            "panelCount": len(self.panels),
            "panels": [panel.to_dict() for panel in self.panels],
            "warnings": list(self.warnings),
            "provenance": self.provenance,
        }
