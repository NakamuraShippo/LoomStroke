"""Glyph outlines produced from a text object (CT-25 F4 ``OutlinedTextPayload``).

``polygons`` are closed fill polygons (one per glyph run, already
rewound so holes render correctly with any fill rule); ``source_meta``
is the original text payload kept for ``Restore Editable Text``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["OutlinedTextPayload"]

Polygon = tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class OutlinedTextPayload:
    object_id: str
    polygons: tuple[Polygon, ...]
    bounds: tuple[float, float, float, float]
    color_rgba: tuple[int, int, int, int]
    source_meta: Mapping[str, Any] = field(default_factory=dict)
    glyph_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.polygons
