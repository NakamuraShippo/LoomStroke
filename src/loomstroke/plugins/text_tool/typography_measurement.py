"""Point Text measurement using the same Japanese glyph layout as painting."""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont

from loomstroke.core.japanese_text.typography import has_japanese_layout, normalize_typography
from loomstroke.core.japanese_text.typography_layout import build_japanese_layout


def measure_japanese_text(
    content: str,
    settings: Any,
    *,
    font: QFont,
    payload: Mapping[str, Any] | None = None,
) -> tuple[float, float] | None:
    """Measure intrinsic size; supplied payload ranges must already match content."""
    values = asdict(settings)
    normalized = normalize_typography(values.pop("typography", None))
    source = deepcopy(dict(payload)) if payload is not None else {}
    source.update(values)
    source["content"] = content
    document = dict(source.get("document") or {})
    document["text"] = content
    source["document"] = document
    layout = dict(source.get("layout") or {})
    layout.update(wrap_mode="none", japanese=normalized)
    source["layout"] = layout
    if not has_japanese_layout(source):
        return None
    # Settings are the current displayed style, including any pending property
    # edit. Remove a stale style map so it cannot override the supplied font.
    source.pop("style_map", None)
    # Alignment positions text inside a frame; it does not add intrinsic size.
    # Neutralize both legacy and paragraph values in this measurement copy so
    # the large unwrapped frame cannot turn center/right offsets into extents.
    source["align"] = "left"
    for paragraph in document.get("paragraphs", []):
        if isinstance(paragraph, dict):
            paragraph["align"] = "left"
    result = build_japanese_layout(
        source, content=content, font=font, rect=QRectF(0.0, 0.0, 1e6, 1e6)
    )
    bounds = result.logical_bounds.united(result.ink_bounds)
    # Occupied cells start after the first-line indent. Include that leading
    # blank space along the writing direction in the Point frame as well.
    extent_width = bounds.width()
    extent_height = bounds.height()
    if settings.vertical:
        extent_height = max(0.0, bounds.bottom()) - min(0.0, bounds.top())
    else:
        extent_width = max(0.0, bounds.right()) - min(0.0, bounds.left())
    width = extent_width + settings.padding_left + settings.padding_right + 4.0
    height = extent_height + settings.padding_top + settings.padding_bottom + 4.0
    return max(8.0, math.ceil(width)), max(8.0, math.ceil(height))
