"""Resolve optional character font overrides without changing document defaults."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

from PySide6.QtGui import QFont, QFontInfo

from ..vector_text_layout import TextLayoutCalculator as Layout
from .annotations import _grapheme_boundaries, _selection_from_boundaries

FONT_KEYS = frozenset({"font_family", "font_style", "font_size_pt"})


def valid_font_value(key, value):
    if key in {"font_family", "font_style"}:
        return value if isinstance(value, str) else None
    if key == "font_size_pt":
        try:
            value = float(value)
            return max(1.0, min(512.0, value)) if isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def font_runs(payload):
    if not isinstance(payload, Mapping):
        return []
    document = payload.get("document", {})
    if not isinstance(document, Mapping):
        return []
    text = document.get("text", payload.get("content", ""))
    runs = document.get("runs", [])
    if not isinstance(text, str) or not isinstance(runs, (list, tuple)):
        return []
    boundaries = _grapheme_boundaries(text)
    result = []
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        values = {key: valid_font_value(key, run[key]) for key in FONT_KEYS if key in run}
        values = {key: value for key, value in values.items() if value is not None}
        bounds = _selection_from_boundaries(len(text), run.get("start"), run.get("end"), boundaries)
        if values and bounds is not None:
            result.append((*bounds, values))
    return sorted(result, key=lambda item: (item[0], item[1]))


class CharacterFonts:
    """Cache resolved QFonts for one layout pass and walk ordered graphemes."""

    def __init__(self, payload, default):
        self.runs = font_runs(payload)
        self.default = default
        self.base = dict(Layout.text_style_defaults(payload))
        for key in FONT_KEYS:
            self.base.setdefault(key, payload.get(key))
        self.cache = {}
        self.cursor = 0

    def at(self, index):
        while self.cursor < len(self.runs) and self.runs[self.cursor][1] <= index:
            self.cursor += 1
        if self.cursor >= len(self.runs) or self.runs[self.cursor][0] > index:
            return self.default
        values = self.runs[self.cursor][2]
        key = tuple(sorted(values.items()))
        if key not in self.cache:
            font = Layout.make_text_font({**self.base, **values, "tracking": 0.0})
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
            self.cache[key] = font
        return self.cache[key]


def font_em(font):
    return float(max(1, QFontInfo(font).pixelSize()))


def has_font_overrides(payload):
    runs = font_runs(payload)
    if not runs:
        return False
    defaults = Layout.text_style_defaults(payload)
    return any(
        value != defaults.get(key, payload.get(key))
        for _start, _end, values in runs
        for key, value in values.items()
    )
