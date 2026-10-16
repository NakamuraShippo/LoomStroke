"""Place selection emphasis in the same glyph stream as body text and ruby."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from unicodedata import category

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QTransform

from .annotations import normalize_annotations
from .character_fonts import font_em, has_font_overrides
from .grapheme_clusters import grapheme_spans
from .typography_layout_types import PositionedGlyph
from .typography_shaping import run_cursor_offsets


class EmphasisPlacer:
    """Reserve annotation lanes and place one mark per eligible grapheme.

    A vertical TCY group is one composition cell. Ruby occupies the inner
    lane; emphasis uses the outer lane on the same side. Stored ranges do
    not make text unbreakable or modify the original text/caret indices.
    """

    def __init__(self, payload: Mapping, text: str, font: QFont, em: float, vertical: bool):
        document = payload.get("document", {})
        raw = document.get("emphasis_runs") if isinstance(document, Mapping) else None
        self.runs = normalize_annotations(raw, text, kind="emphasis_runs")
        self.ends = [run["end"] for run in self.runs]
        self.text, self.font, self.em, self.vertical = text, font, em, vertical
        self.inner = (0.0, 0.0)
        self.mixed_fonts = has_font_overrides(payload)

    def lanes(self, before: float, after: float) -> tuple[float, float]:
        self.inner = before, after
        sizes = [0.0, 0.0]
        for run in self.runs:
            side = 0 if run["position"] == "before" else 1
            sizes[side] = max(sizes[side], self.em * (run["size_ratio"] + run["gap_em"]))
        return before + sizes[0], after + sizes[1]

    def place(self, glyphs: list[PositionedGlyph]) -> list[PositionedGlyph]:
        marks = []
        if not self.runs:
            return marks
        groups = set()
        for glyph in glyphs:
            if glyph.role not in {"base", "tcy"}:
                continue
            if glyph.group_range is not None:
                if glyph.group_range in groups:
                    continue
                groups.add(glyph.group_range)
            for start, end, cell in self._cells(glyph):
                index = bisect_right(self.ends, start)
                if index >= len(self.runs):
                    continue
                run = self.runs[index]
                if run["start"] >= end or not _eligible(self.text[start:end]):
                    continue
                marks.append(self._mark(run, start, end, cell, glyph.line_index, glyph.font))
        return marks

    def _cells(self, glyph: PositionedGlyph):
        if glyph.group_range is not None and glyph.group_cell is not None:
            yield *glyph.group_range, glyph.group_cell
            return
        text = self.text[glyph.start : glyph.end]
        spans = grapheme_spans(text)
        if glyph.role == "tcy" or len(spans) == 1:
            yield glyph.start, glyph.end, glyph.cell
            return
        offsets = run_cursor_offsets(text, glyph.font)
        total = max(0.01, offsets[-1])
        extent = glyph.cell.height() if self.vertical else glyph.cell.width()
        for start, end in spans:
            left, right = offsets[start] / total * extent, offsets[end] / total * extent
            cell = QRectF(glyph.cell)
            if self.vertical:
                cell.setTop(glyph.cell.top() + left)
                cell.setBottom(glyph.cell.top() + right)
            else:
                cell.setLeft(glyph.cell.left() + left)
                cell.setRight(glyph.cell.left() + right)
            yield glyph.start + start, glyph.start + end, cell

    def _mark(self, run, start, end, parent, line_index, body_font):
        symbols = {
            "auto": "﹅" if self.vertical else "•",
            "sesame": "﹅",
            "dot": "•",
            "circle": "○",
        }
        symbol = symbols[run["mark"]]
        em = font_em(body_font) if self.mixed_fonts else self.em
        size = em * run["size_ratio"]
        before = run["position"] == "before"
        gap = self.inner[0 if before else 1] + em * run["gap_em"]
        if self.vertical:
            x = parent.right() + gap if before else parent.left() - gap - size
            cell = QRectF(x, parent.center().y() - size / 2, size, size)
        else:
            y = parent.top() - gap - size if before else parent.bottom() + gap
            cell = QRectF(parent.center().x() - size / 2, y, size, size)
        font = QFont(body_font if self.mixed_fonts else self.font)
        font.setPixelSize(max(1, round(size)))
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
        ink = QFontMetricsF(font).tightBoundingRect(symbol)
        scale = min(1.0, size / max(0.01, ink.width()), size / max(0.01, ink.height()))
        transform = QTransform().translate(cell.center().x(), cell.center().y())
        transform.scale(scale, scale)
        transform.translate(-ink.center().x(), -ink.center().y())
        return PositionedGlyph(symbol, font, transform, start, end, "emphasis", line_index, cell)


def _eligible(text: str) -> bool:
    # JLReq 3.3.9 excludes full stops, commas and opening/closing brackets.
    return any(
        not char.isspace()
        and char not in "、。\uff0c\uff0e,."
        and category(char) not in {"Ps", "Pe", "Pi", "Pf", "Cc", "Cf", "Mn", "Me"}
        for char in text
    )
