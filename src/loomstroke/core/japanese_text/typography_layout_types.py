"""Canvas-logical placement results shared by Japanese text painting and editing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QTransform

if TYPE_CHECKING:
    from ..text_line_geometry import TextLineGeometry


@dataclass(frozen=True, slots=True)
class PositionedGlyph:
    text: str
    font: QFont
    transform: QTransform
    start: int
    end: int
    role: str = "base"
    line_index: int = 0
    cell: QRectF = field(default_factory=QRectF)
    group_range: tuple[int, int] | None = None
    group_cell: QRectF | None = None


@dataclass(frozen=True, slots=True)
class JapaneseLayoutResult:
    lines: tuple[TextLineGeometry, ...]
    glyphs: tuple[PositionedGlyph, ...]
    ink_bounds: QRectF
    logical_bounds: QRectF
    frame: QRectF
    em: float
    diagnostics: tuple[str, ...] = ()
    carets: dict[int, QRectF] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LayoutAtom:
    """One breakable unit; ruby groups and sideways words contain original indices."""

    text: str
    start: int
    end: int
    advance: float
    kind: str = "base"
    category: str = "other"
    tracking: float = 0.0
    children: tuple[LayoutAtom, ...] = ()
    ruby: dict[str, Any] | None = None
    line_start_advance: float | None = None
    line_end_advance: float | None = None
    font: QFont | None = None
    em: float = 0.0
    geometry: dict[str, float] = field(default_factory=dict)
    special: dict[str, Any] = field(default_factory=dict)
