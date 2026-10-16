"""Place ruby and tate-chu-yoko in the shared logical text layout."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QTransform

from .grapheme_clusters import grapheme_spans
from .typography import finite_number
from .typography_layout_types import PositionedGlyph
from .typography_shaping import run_cursor_offsets


@dataclass(frozen=True, slots=True)
class _RubyShape:
    text: str
    font: QFont
    metrics: QFontMetricsF
    clusters: tuple[tuple[int, int], ...]
    offsets: tuple[float, ...]
    advance: float
    em: float


def _ruby_shape(run: Mapping[str, Any], font: QFont, em: float, vertical: bool) -> _RubyShape:
    value = run.get("ruby", "")
    text = value if isinstance(value, str) else ""
    ratio = finite_number(run.get("size_ratio"), 0.5, 0.1, 2.0)
    ruby_font = QFont(font)
    if run.get("use_ruby_glyphs") is True:
        ruby_font.setFeature(QFont.Tag("ruby"), 1)
    if ruby_font.pointSizeF() > 0:
        ruby_font.setPointSizeF(ruby_font.pointSizeF() * ratio)
    else:
        ruby_font.setPixelSize(max(1, round(ruby_font.pixelSize() * ratio)))
    ruby_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
    if vertical:
        ruby_font.setFeature(QFont.Tag("vert"), 1)
    metrics = QFontMetricsF(ruby_font)
    clusters = grapheme_spans(text)
    ruby_em = max(0.01, em * ratio)
    if vertical:
        offsets = tuple(index * ruby_em for index in range(len(clusters) + 1))
    elif run.get("align", "center") in {"space-around", "space-between"}:
        # Distributed readings are painted one grapheme at a time, so their
        # envelope must use those same advances instead of whole-run kerning.
        distributed_offsets = [0.0]
        for start, end in clusters:
            distributed_offsets.append(
                distributed_offsets[-1] + metrics.horizontalAdvance(text[start:end])
            )
        offsets = tuple(distributed_offsets)
    else:
        positions = run_cursor_offsets(text, ruby_font)
        offsets = (
            *(float(positions[start]) for start, _ in clusters),
            metrics.horizontalAdvance(text),
        )
    return _RubyShape(text, ruby_font, metrics, clusters, offsets, offsets[-1], ruby_em)


def ruby_required_advance(
    run: Mapping[str, Any],
    *,
    font: QFont,
    em: float,
    vertical: bool,
    base_advance: float,
    allow_before: bool,
    allow_after: bool,
) -> float:
    """Reserve ruby overflow without spilling into a prohibited neighbour.

    The caller decides whether adjacent content permits overhang. A centred
    reading can use only the smaller of the two side allowances, because
    its placement remains centred in the reserved parent cell. Start/end
    alignment can use only the opposite edge. Line ends pass False.
    """
    shape = _ruby_shape(run, font, em, vertical)
    base = max(0.0, base_advance)
    budget = max(0.0, em) * finite_number(run.get("overhang_em"), 0.5, 0.0, 2.0)
    before = budget if allow_before else 0.0
    after = budget if allow_after else 0.0
    align = run.get("align", "center")
    if align == "start":
        available = after
    elif align == "end":
        available = before
    else:
        available = 2.0 * min(before, after)
    return max(base, shape.advance - available)


def _ruby_positions(shape: _RubyShape, extent: float, align: Any) -> tuple[float, ...]:
    spare = extent - shape.advance
    count = len(shape.clusters)
    if align == "start":
        origin = 0.0
    elif align == "end":
        origin = spare
    else:
        origin = spare * 0.5
    if spare > 0.0 and count:
        if align == "space-around":
            extra = spare / count
            return tuple(shape.offsets[i] + (i + 0.5) * extra for i in range(count))
        if align == "space-between" and count > 1:
            extra = spare / (count - 1)
            return tuple(shape.offsets[i] + i * extra for i in range(count))
    return tuple(origin + shape.offsets[i] for i in range(count))


def place_ruby(
    run: Mapping[str, Any],
    *,
    parent_cell: QRectF,
    font: QFont,
    em: float,
    vertical: bool,
    line_index: int,
) -> tuple[PositionedGlyph, ...]:
    """Place a normalized reading beside its already-reserved parent cell."""
    shape = _ruby_shape(run, font, em, vertical)
    if not shape.text or not shape.clusters:
        return ()
    start, end = int(run.get("start", 0)), int(run.get("end", 0))
    gap = max(0.0, em) * finite_number(run.get("gap_em"), 0.05, 0.0, 2.0)
    before = run.get("position", "before") != "after"
    extent = parent_cell.height() if vertical else parent_cell.width()
    align = run.get("align", "center")
    positions = _ruby_positions(shape, extent, align)
    metrics = shape.metrics
    if not vertical and align not in {"space-around", "space-between"}:
        # Preserve the font's shaping/kerning for an undistributed reading.
        baseline = (
            parent_cell.top() - gap - metrics.descent()
            if before
            else parent_cell.bottom() + gap + metrics.ascent()
        )
        left = parent_cell.left() + positions[0] - shape.offsets[0]
        return (
            PositionedGlyph(
                shape.text,
                shape.font,
                QTransform.fromTranslate(left, baseline),
                start,
                end,
                role="ruby",
                line_index=line_index,
                cell=QRectF(left, baseline - metrics.ascent(), shape.advance, metrics.height()),
            ),
        )
    result = []
    for index, (left, right) in enumerate(shape.clusters):
        text = shape.text[left:right]
        if vertical:
            cell_x = parent_cell.right() + gap if before else parent_cell.left() - gap - shape.em
            cell_y = parent_cell.top() + positions[index]
            cell = QRectF(cell_x, cell_y, shape.em, shape.em)
            draw_x = cell_x + (shape.em - metrics.horizontalAdvance(text)) * 0.5
            baseline = cell_y + (shape.em - metrics.height()) * 0.5 + metrics.ascent()
        else:
            draw_x = parent_cell.left() + positions[index]
            baseline = (
                parent_cell.top() - gap - metrics.descent()
                if before
                else parent_cell.bottom() + gap + metrics.ascent()
            )
            cell = QRectF(
                draw_x,
                baseline - metrics.ascent(),
                metrics.horizontalAdvance(text),
                metrics.height(),
            )
        result.append(
            PositionedGlyph(
                text,
                shape.font,
                QTransform.fromTranslate(draw_x, baseline),
                start,
                end,
                role="ruby",
                line_index=line_index,
                cell=cell,
            )
        )
    return tuple(result)


def place_tcy(
    text: str,
    *,
    start: int,
    end: int,
    font: QFont,
    cell: QRectF,
    line_index: int,
) -> tuple[tuple[PositionedGlyph, ...], dict[int, QRectF]]:
    """Keep a horizontal run upright inside one cell, including its carets."""
    if not text or cell.width() <= 0.0 or cell.height() <= 0.0:
        return (), {}
    horizontal_font = QFont(font)
    horizontal_font.unsetFeature(QFont.Tag("vert"))
    horizontal_font.unsetFeature(QFont.Tag("vrt2"))
    horizontal_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
    metrics = QFontMetricsF(horizontal_font)
    offsets = run_cursor_offsets(text, horizontal_font)
    advance = max(0.01, metrics.horizontalAdvance(text))
    height = max(0.01, metrics.height())
    envelope = QRectF(0.0, -metrics.ascent(), advance, height).united(
        metrics.tightBoundingRect(text)
    )
    scale = min(1.0, cell.width() / envelope.width(), cell.height() / envelope.height())
    x = cell.center().x() - envelope.center().x() * scale
    baseline = cell.center().y() - envelope.center().y() * scale
    top = baseline - metrics.ascent() * scale
    transform = QTransform().translate(x, baseline).scale(scale, scale)
    glyph = PositionedGlyph(
        text,
        horizontal_font,
        transform,
        start,
        end,
        role="tcy",
        line_index=line_index,
        cell=QRectF(cell),
    )
    caret_width = min(1.0, cell.width())
    carets = {
        (start + index): QRectF(
            min(cell.right() - caret_width, max(cell.left(), x + float(offsets[index]) * scale)),
            top,
            caret_width,
            height * scale,
        )
        for index in range(len(text) + 1)
    }
    return (glyph,), carets
