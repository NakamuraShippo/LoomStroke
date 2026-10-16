"""Resolve font-provided vertical alternates before using geometric fallbacks."""

from __future__ import annotations

from PySide6.QtGui import QFont, QTextLayout


class VerticalFontForms:
    """Keep font shaping and its small alternate cache local to one paint call.

    ``vert`` supplies punctuation, small-kana and prolonged-sound-mark forms
    drawn by the type designer. It does not enable ``vrt2``'s Latin rotation,
    which would override the document's explicit half-width rotation setting.
    Normal ideographs can use ``font`` directly. Only characters requiring a
    geometric fallback need the extra shaping comparison, once per distinct
    grapheme in this paint call.
    """

    def __init__(self, font: QFont) -> None:
        self.base_font = QFont(font)
        self.font = QFont(font)
        self.font.setFeature(QFont.Tag("vert"), 1)
        self._alternates: dict[str, bool] = {}

    def has_alternate(self, text: str) -> bool:
        result = self._alternates.get(text)
        if result is None:
            result = self._glyph_signature(text, self.base_font) != self._glyph_signature(
                text, self.font
            )
            self._alternates[text] = result
        return result

    @staticmethod
    def _glyph_signature(text: str, font: QFont) -> tuple[tuple[str, str, tuple[int, ...]], ...]:
        layout = QTextLayout(text, font)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(1e12)
        layout.endLayout()
        return tuple(
            (run.rawFont().familyName(), run.rawFont().styleName(), tuple(run.glyphIndexes()))
            for run in layout.glyphRuns()
        )
