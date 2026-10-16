"""Source-preserving labels and local fallback for the filter pen UI."""

from __future__ import annotations

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.ui.filter_pen_translations import FILTER_PEN_JA as JA_LABELS
from loomstroke.ui.translations import translate_with_fallback, translated_literal_domain

__all__ = ["JA_LABELS", "filter_text", "ts"]


def ts(source: str) -> str:
    return translated_literal_domain("FilterPen", source, use_fallback=True)


def filter_text(source: str) -> str:
    translated = translate_with_fallback("FilterPanel", source)
    return translate_with_fallback("FilterPen", source) if translated == source else translated


# Compatibility aliases for existing imports.
effect_text = filter_text

_bind_legacy_names(__name__, {"effect_text": "filter_text"})
