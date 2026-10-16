"""Necessary capacity bounds for ComicPagePlan's fixed native text settings."""

from __future__ import annotations

from collections import Counter
from math import floor

from PySide6.QtGui import QFont, QFontInfo, QFontMetricsF

from loomstroke.core.japanese_text.grapheme_clusters import grapheme_spans
from loomstroke.core.japanese_text.typography_atoms import category
from loomstroke.core.vector_text_layout import TextLayoutCalculator


class ComicTextCapacity:
    """Reject impossible sizes without assuming that wrapping is monotone.

    This bound only covers the settings authored by comic_native_text: one
    frame, 1.15-em line steps, zero tracking, standard punctuation compression,
    no annotations or character overrides. The native layout remains the fit
    oracle for every size not rejected here.
    """

    def __init__(self, text, family, vertical, rotate, width, height):
        self.family = family
        self.along, self.cross = (height, width) if vertical else (width, height)
        self.paragraphs = 1
        self.em_units = 0.0
        self.measured: Counter[tuple[str, float]] = Counter()
        self.has_text = bool(text)
        latin = []

        def flush_latin():
            if latin:
                # Native sideways Latin is one shaped run. Measuring individual
                # characters would overestimate ligatures and kerning.
                self.measured[("".join(latin), 1.0)] += 1
                latin.clear()

        for start, end in grapheme_spans(text):
            value = text[start:end]
            if value in {"\r", "\n", "\r\n"}:
                flush_latin()
                self.paragraphs += 1
                continue
            group = category(value)
            if group == "latin" and (not vertical or rotate):
                latin.append(value)
                continue
            flush_latin()
            if group in {"opening", "closing", "stop", "middle"}:
                # Edge trimming and compression share a half-em allowance;
                # the punctuation body retains at least the other half.
                self.em_units += 0.5
            elif vertical or group == "japanese":
                self.em_units += 0.5 if group == "space" and value != "\u3000" else 1.0
            else:
                # Half-width spaces may compress by half their advance. Other
                # horizontal graphemes retain their measured native advance.
                factor = 0.5 if group == "space" and value != "\u3000" else 1.0
                self.measured[(value, factor)] += 1
        flush_latin()

    def cannot_fit(self, size: int) -> bool:
        if not self.has_text:
            return False
        pixel_font = QFont(self.family) if self.family else QFont()
        pixel_font.setPixelSize(size)
        font = TextLayoutCalculator.make_text_font(
            {"font_family": self.family, "font_size_pt": QFontInfo(pixel_font).pointSizeF()}
        )
        em = float(max(1, QFontInfo(font).pixelSize()))
        # Use the outer eight-pixel inset. The measured ink guards can only
        # reduce capacity, so ignoring them never rejects a fitting layout.
        rows = max(0, floor((self.cross - em + 1e-6) / (em * 1.15)) + 1)
        if rows < self.paragraphs:
            # Empty paragraphs also occupy an em cell in _place_line.
            return True
        minimum = self.em_units * em
        if minimum > rows * self.along + 1e-6:
            return True
        metrics = QFontMetricsF(font)
        for (value, factor), count in self.measured.items():
            minimum += max(0.0, metrics.horizontalAdvance(value)) * factor * count
        return minimum > rows * self.along + 1e-6
