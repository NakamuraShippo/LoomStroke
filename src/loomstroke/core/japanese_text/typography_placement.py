"""Convert Japanese line atoms to glyph transforms and original-index carets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QTransform

from ..vertical_font_forms import VerticalFontForms
from ..vertical_glyph_fallback import glyph_draw_spec
from .character_fonts import font_em
from .character_geometry import place_geometry
from .geometry_tcy import place_geometry_tcy
from .grapheme_clusters import grapheme_spans
from .special_layout import place_special
from .typography_annotations_layout import place_ruby, place_tcy
from .typography_layout_types import LayoutAtom, PositionedGlyph
from .typography_mixed_tcy import place_mixed_tcy
from .typography_shaping import run_cursor_offsets


class GlyphPlacer:
    def __init__(self, font: QFont, em: float, vertical: bool, options: Mapping[str, Any]) -> None:
        self.font, self.em, self.vertical, self.options = font, em, vertical, options
        self.metrics = QFontMetricsF(font)
        self.forms = VerticalFontForms(font)

    def place(
        self, atom: LayoutAtom, cell: QRectF, line_index: int
    ) -> tuple[list[PositionedGlyph], dict[int, QRectF]]:
        if atom.geometry:
            return place_geometry(self, atom, cell, line_index)
        if atom.kind in {"warichu", "jidori"}:
            return place_special(self, atom, cell, line_index)
        if atom.font is not None and atom.font != self.font:
            return GlyphPlacer(atom.font, self.em, self.vertical, self.options).place(
                atom, cell, line_index
            )
        if atom.kind == "tcy" and self.vertical:
            return self._tcy(atom, cell, line_index)
        if atom.ruby is not None:
            return self._ruby_parent(atom, cell, line_index)
        return self._base(atom, cell, line_index)

    def _tcy(self, atom, cell, line_index):
        if any(child.geometry for child in atom.children):
            return place_geometry_tcy(self, atom, cell, line_index)
        if len({child.font.toString() for child in atom.children if child.font is not None}) > 1:
            return place_mixed_tcy(atom, cell, line_index)
        glyphs, carets = place_tcy(
            atom.text,
            start=atom.start,
            end=atom.end,
            font=self.font,
            cell=cell,
            line_index=line_index,
        )
        return list(glyphs), carets

    def _base(self, atom, cell, line_index):
        font = QFont(self.font)
        metrics = self.metrics
        glyph, angle, dx, dy = atom.text, 0.0, 0.0, 0.0
        if atom.kind == "latin":
            spacing = atom.children[0].tracking if atom.children else 0.0
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
            metrics = QFontMetricsF(font)
            angle = 90.0 if self.vertical else 0.0
        elif self.vertical:
            glyph, angle, dx, dy = glyph_draw_spec(
                atom.text,
                metrics=metrics,
                column_step=atom.em or self.em,
                char_step=atom.em or self.em,
                vertical_flow=str(self.options.get("_vertical_flow", "left")),
                rotate_half_width=bool(self.options.get("_rotate_half_width", False)),
                supports_char=metrics.inFont,
            )
            if (glyph != atom.text or angle or dx or dy) and self.forms.has_alternate(atom.text):
                glyph, angle, dx, dy = atom.text, 0.0, 0.0, 0.0
                font = self.forms.font
                metrics = QFontMetricsF(font)
        advance = metrics.horizontalAdvance(glyph)
        if angle:
            transform = QTransform().translate(cell.center().x(), cell.center().y()).rotate(angle)
            transform.translate(-advance * 0.5, (metrics.ascent() - metrics.descent()) * 0.5)
        else:
            x = cell.left() + (cell.width() - advance) * 0.5 if self.vertical else cell.left()
            baseline = cell.top() + (self.em - metrics.height()) * 0.5 + metrics.ascent()
            transform = QTransform.fromTranslate(x + dx, baseline + dy)
        placed = PositionedGlyph(
            glyph,
            font,
            transform,
            atom.start,
            atom.end,
            role="base",
            line_index=line_index,
            cell=QRectF(cell),
        )
        offsets = run_cursor_offsets(atom.text, font) if atom.kind == "latin" else None
        carets = {}
        total = cell.height() if self.vertical else cell.width()
        for start, end in grapheme_spans(atom.text):
            along = float(offsets[start]) if offsets is not None else 0.0
            for index in range(start, end):
                carets[atom.start + index] = self.caret(cell, along)
        carets[atom.end] = self.caret(cell, total)
        return [placed], carets

    def caret(self, cell: QRectF, along: float) -> QRectF:
        if self.vertical:
            return QRectF(cell.left(), cell.top() + along, cell.width(), 1.0)
        return QRectF(cell.left() + along, cell.top(), 1.0, cell.height())

    def _ruby_parent(
        self, atom: LayoutAtom, cell: QRectF, line_index: int
    ) -> tuple[list[PositionedGlyph], dict[int, QRectF]]:
        natural = sum(child.advance + child.tracking for child in atom.children)
        extent = cell.height() if self.vertical else cell.width()
        spare = max(0.0, extent - natural)
        # Group parents share extra width between graphemes; single parents
        # remain centred. This leaves the reading's required footprint free.
        extra = spare / (len(atom.children) - 1) if len(atom.children) > 1 else 0.0
        cursor = spare * 0.5 if len(atom.children) == 1 else 0.0
        glyphs, carets = [], {}
        for child in atom.children:
            child_cell = (
                QRectF(cell.left(), cell.top() + cursor, cell.width(), child.advance)
                if self.vertical
                else QRectF(cell.left() + cursor, cell.top(), child.advance, cell.height())
            )
            child_glyphs, child_carets = self.place(child, child_cell, line_index)
            glyphs.extend(child_glyphs)
            carets.update(child_carets)
            cursor += child.advance + child.tracking + extra
        glyphs.extend(
            place_ruby(
                atom.ruby or {},
                parent_cell=cell,
                font=self.font,
                em=font_em(self.font) if atom.font is not None else self.em,
                vertical=self.vertical,
                line_index=line_index,
            )
        )
        return glyphs, carets
