"""Compose span/split paragraph bands through the shared column layout."""

from collections.abc import Mapping
from dataclasses import replace

from PySide6.QtCore import QRectF

from ..vector_text_layout import TextLayoutCalculator as Layout
from .frame_flow import payload_flow
from .line_breaker import JapaneseLineBreaker
from .paragraph_styles import paragraph_style
from .typography_layout_types import JapaneseLayoutResult


def paragraph_column_count(raw):
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 32 else 0


def has_paragraph_columns(payload):
    document = payload.get("document", {}) if isinstance(payload, Mapping) else {}
    records = document.get("paragraphs", []) if isinstance(document, Mapping) else []
    return isinstance(records, (list, tuple)) and any(
        isinstance(record, Mapping)
        and isinstance(record.get("style_overrides"), Mapping)
        and paragraph_column_count(record["style_overrides"].get("paragraph_columns"))
        for record in records
    )


def paragraph_bands(payload, text, font, frame, shape, compose):
    if not has_paragraph_columns(payload) or payload.get("linked_story"):
        return None
    from .linked_story import slice_payload  # noqa: PLC0415

    options = payload_flow(payload)
    blocks = []
    for start, end, following in JapaneseLineBreaker.paragraphs(text):
        columns = paragraph_style(payload, start).get("paragraph_columns", 0) or options["columns"]
        if blocks and blocks[-1][3] == columns:
            blocks[-1] = (blocks[-1][0], end, following, columns)
        else:
            blocks.append((start, end, following, columns))
    vertical = bool(
        Layout.text_style_defaults(payload).get("vertical", payload.get("vertical", False))
    )
    leftward = Layout.text_vertical_flow_from_payload(payload) == "left"
    remaining = QRectF(frame)
    lines, glyphs, carets, diagnostics = [], [], {}, []
    ink, logical, em = QRectF(), QRectF(), 1.0
    for index, (start, end, following, columns) in enumerate(blocks):
        local = slice_payload(payload, start, end)
        local["content"] = local["document"]["text"] = text[start:end]
        for record in local["document"].get("paragraphs", []):
            if isinstance(record, dict) and isinstance(record.get("style_overrides"), dict):
                record["style_overrides"].pop("paragraph_columns", None)
        local.setdefault("layout", {})["text_flow"] = {
            **options,
            "columns": columns,
            "balance": index < len(blocks) - 1 or options["balance"],
        }
        result = compose(
            local, content=text[start:end], font=font, rect=remaining, shape_path=shape
        )
        line_offset = len(lines)
        lines.extend(
            replace(line, start=line.start + start, next_start=line.next_start + start)
            for line in result.lines
        )
        glyphs.extend(
            replace(
                g,
                start=g.start + start,
                end=g.end + start,
                line_index=g.line_index + line_offset,
                group_range=(
                    (g.group_range[0] + start, g.group_range[1] + start) if g.group_range else None
                ),
            )
            for g in result.glyphs
        )
        carets.update({position + start: rect for position, rect in result.carets.items()})
        if end in carets:
            carets.update({position: QRectF(carets[end]) for position in range(end, following)})
        ink, logical, em = (
            ink.united(result.ink_bounds),
            logical.united(result.logical_bounds),
            result.em,
        )
        diagnostics.extend(result.diagnostics)
        if shape is not None and any(d.startswith("shape_overflow") for d in result.diagnostics):
            break
        _advance_band(remaining, result.logical_bounds, vertical, leftward)
        if remaining.width() <= 0 or remaining.height() <= 0:
            diagnostics.append("line_overflow")
            remaining.setSize(frame.size())
    return JapaneseLayoutResult(
        tuple(lines),
        tuple(glyphs),
        ink,
        logical,
        QRectF(frame),
        em,
        tuple(dict.fromkeys(diagnostics)),
        carets,
    )


def _advance_band(remaining, occupied, vertical, leftward):
    if not vertical:
        remaining.setTop(occupied.bottom())
    elif leftward:
        remaining.setRight(occupied.left())
    else:
        remaining.setLeft(occupied.right())
