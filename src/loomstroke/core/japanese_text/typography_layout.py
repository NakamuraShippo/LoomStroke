"""Optional Japanese composition shared by raster paint, bounds and editing.

Spacing follows JLReq 3.1.2/3.1.4/3.1.5/3.1.9, mixed runs 3.2.6,
annotations 3.2.5/3.3 and bounded line adjustment 3.8. Original content and
Python indices are never normalized or rewritten by layout.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontInfo, QFontMetricsF, QPainterPath

from ..text_line_geometry import TextLineGeometry
from ..vector_text_layout import TextLayoutCalculator as Layout
from .character_fonts import font_em
from .character_styles import color_runs
from .column_balance import balanced_layout
from .frame_flow import ColumnFlow
from .grapheme_clusters import grapheme_spans
from .legacy_color_layout import legacy_color_layout
from .line_breaker import JapaneseLineBreaker
from .paragraph_columns import paragraph_bands
from .paragraph_direction import reverse_columns
from .paragraph_styles import paragraph_style
from .story_metadata import validated_story
from .typography import has_japanese_layout, typography_from_payload
from .typography_atoms import build_atoms
from .typography_emphasis import EmphasisPlacer
from .typography_frame import frame_segments, resolve_shape, shape_contains_rect
from .typography_layout_types import JapaneseLayoutResult, PositionedGlyph
from .typography_lines import BrokenLine, LineComposer
from .typography_placement import GlyphPlacer
from .typography_range_values import RangeValueCursor


def build_japanese_layout(  # noqa: PLR0912, PLR0915 - paragraph/row/segment composition
    payload: Mapping[str, Any],
    *,
    content: str | None = None,
    font: QFont | None = None,
    rect: QRectF | tuple[float, float, float, float] | None = None,
    shape_path: QPainterPath | None = None,
) -> JapaneseLayoutResult:
    """Place glyph baselines in unrotated canvas logical coordinates.

    An explicit ``rect`` already has padding applied. With no rect, use the
    payload frame and its padding. Object/view transforms belong to callers.
    ``logical_bounds`` encloses occupied cells, never the whole input frame.
    """
    text = Layout.text_content_from_payload(payload) if content is None else content
    base_font = QFont(font) if font is not None else Layout.make_text_font(payload)
    frame = _frame(payload, rect)
    from .story_composition import compose_extended_story  # noqa: PLC0415

    story_result = compose_extended_story(payload, text, base_font, frame, build_japanese_layout)
    if story_result is not None:
        return story_result
    if shape_path is None:
        shape_path = resolve_shape(payload, frame)
    bands = paragraph_bands(
        payload,
        text,
        base_font,
        frame,
        shape_path,
        build_japanese_layout,
    )
    if bands is not None:
        return bands
    balanced = balanced_layout(
        payload,
        text,
        base_font,
        frame,
        shape_path,
        build_japanese_layout,
    )
    if balanced is not None:
        return balanced
    if (
        color_runs(payload)
        and not has_japanese_layout(payload)
        and not payload.get("_composing_story_member")
    ):
        return legacy_color_layout(
            payload,
            text,
            base_font,
            frame,
            shape_path,
        )
    base_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
    metrics = QFontMetricsF(base_font)
    em = float(max(1, QFontInfo(base_font).pixelSize()))
    frame = _frame(payload, rect)
    style = Layout.text_style_defaults(payload)
    vertical = bool(style.get("vertical", payload.get("vertical", False)))
    flow = Layout.text_vertical_flow_from_payload(payload)
    options = _options(payload, style, flow)
    paragraph_ranges = list(JapaneseLineBreaker.paragraphs(text))
    starts = [span[0] for span in paragraph_ranges]
    paragraph_settings = [
        _options(payload, style, flow, paragraph_style(payload, start).get("typography"))
        for start in starts
    ]

    def options_at(index):
        return paragraph_settings[max(0, bisect_right(starts, index) - 1)] if starts else options

    diagnostics: list[str] = []
    atoms = build_atoms(
        text,
        payload,
        font=base_font,
        em=em,
        vertical=vertical,
        options=options,
        diagnostics=diagnostics,
        options_at=options_at,
    )
    # A mixed-size text frame shares a grid large enough for its largest body.
    # Individual glyphs retain their requested size and remain centred in it.
    em = max((atom.em for atom in atoms), default=em) or em
    before, after = _annotation_lanes(atoms, em, metrics, vertical)
    emphasis = EmphasisPlacer(payload, text, base_font, em, vertical)
    before, after = emphasis.lanes(before, after)
    columns = ColumnFlow(payload, frame, em, vertical, flow, before, after)
    cross = columns.origin()
    step_base = (
        em
        if options["enabled"]
        else max(
            (QFontMetricsF(atom.font or base_font).lineSpacing() for atom in atoms),
            default=metrics.lineSpacing(),
        )
    )
    line_height = Layout.text_line_height_from_payload(payload)
    height_runs = Layout.text_range_runs_from_payload(payload, "line_height_runs") or []
    height_values = RangeValueCursor(height_runs, "line_height", line_height)
    wrap = (
        Layout.text_wrap_mode_from_payload(
            payload, text_mode=Layout.text_mode_from_payload(payload)
        )
        != "none"
    )
    align = Layout.text_align_from_payload(payload)
    placer = GlyphPlacer(base_font, em, vertical, options)
    lines: list[TextLineGeometry] = []
    line_members, line_regions, member_carets, member_bounds = [], [], {}, {}
    glyphs: list[PositionedGlyph] = []
    carets: dict[int, QRectF] = {}
    logical = QRectF()
    atom_cursor = 0
    for start, end, next_start in paragraph_ranges:
        first_line, first_glyph = len(lines), len(glyphs)
        options = options_at(start)
        placer = GlyphPlacer(base_font, em, vertical, options)
        paragraph_align = paragraph_style(payload, start).get("align", align)
        while atom_cursor < len(atoms) and atoms[atom_cursor].end <= start:
            atom_cursor += 1
        paragraph_stop = atom_cursor
        while paragraph_stop < len(atoms) and atoms[paragraph_stop].end <= end:
            paragraph_stop += 1
        paragraph = atoms[atom_cursor:paragraph_stop]
        atom_cursor = paragraph_stop
        step_base = (
            em
            if options["enabled"]
            else max(
                (QFontMetricsF(atom.font or base_font).lineSpacing() for atom in paragraph),
                default=metrics.lineSpacing(),
            )
        )
        paragraph_options = (
            dict(options, line_adjustment="justify") if paragraph_align == "justify" else options
        )
        composer = LineComposer(paragraph, paragraph_options, em)
        cursor, first = 0, True
        while cursor < len(paragraph) or first:
            cross = columns.advance(cross)
            if shape_path is not None and (
                (vertical and (cross < frame.left() or cross + em > frame.right()))
                or (not vertical and cross + em > frame.bottom())
            ):
                diagnostics.append(
                    f"shape_overflow:{paragraph[cursor].start if paragraph else start}"
                )
                break
            segments = frame_segments(
                columns.frame,
                shape_path,
                vertical=vertical,
                cross=cross,
                em=em,
                before=before,
                after=after,
            )
            row_start, row_end = end, start
            for left, right in segments:
                indent = float(options["first_line_indent_em"]) * em if first else 0.0
                limit = max(0.01, right - left - indent)
                broken = composer.take(cursor, limit, wrap=wrap or shape_path is not None)
                origin = left + indent
                if paragraph_align == "center":
                    origin += max(0.0, (limit - broken.extent) * 0.5)
                elif paragraph_align == "right":
                    origin += max(0.0, limit - broken.extent)
                line_start = paragraph[cursor].start if paragraph else start
                line_end = paragraph[broken.end - 1].end if broken.end > cursor else end
                following = next_start if broken.end >= len(paragraph) else line_end
                line, placed, insertion, occupied = _place_line(
                    broken,
                    text,
                    line_start,
                    line_end,
                    following,
                    origin,
                    cross,
                    em,
                    placer,
                    len(lines),
                )
                placed.extend(emphasis.place(placed))
                if shape_path is not None and not _inside_shape(placed, shape_path):
                    continue
                lines.append(line)
                line_members.append(columns.member)
                line_regions.append(columns.index)
                member_carets.setdefault(columns.member, {}).update(insertion)
                member_bounds[columns.member] = member_bounds.get(columns.member, QRectF()).united(
                    occupied
                )
                glyphs.extend(placed)
                carets.update(insertion)
                logical = logical.united(occupied)
                row_start, row_end = min(row_start, line_start), max(row_end, line_end)
                if broken.hanging:
                    diagnostics.append(f"hanging:{line_end}")
                elif broken.overflow:
                    diagnostics.append(f"line_overflow:{line_start}:{line_end}")
                cursor, first = broken.end, False
                if cursor >= len(paragraph):
                    for separator in range(end, next_start):
                        carets[separator] = insertion.get(
                            line_end, placer.caret(occupied, broken.extent)
                        )
                    break
            factor = line_height
            if row_end > row_start:
                factor = height_values.at(row_start, row_end)
            step = (
                max(step_base * factor, em + before + after)
                if before or after
                else step_base * factor
            )
            step = columns.step(step)
            cross += -step if vertical and flow == "left" else step
            if not paragraph and not first:
                break
            if shape_path is None and not segments:
                break
        if vertical and paragraph_style(payload, start).get("vertical_flow", flow) != flow:
            reverse_columns(lines, glyphs, carets, first_line, first_glyph, end, line_regions)
            for index in range(first_line, len(lines)):
                line = lines[index]
                stop = lines[index + 1].start if index + 1 < len(lines) else end + 1
                member_carets[line_members[index]].update(
                    (position, carets[position])
                    for position in range(line.start, stop)
                    if position in carets
                )
            if shape_path is not None and not _inside_shape(glyphs[first_glyph:], shape_path):
                diagnostics.append(f"shape_overflow:{start}")
    story = validated_story(payload.get("linked_story"))
    if story is not None:
        member = story.get("member")
        chosen = [i for i, value in enumerate(line_members) if value == member]
        indexes = {old: new for new, old in enumerate(chosen)}
        lines = [lines[i] for i in chosen]
        glyphs = [
            replace(g, line_index=indexes[g.line_index]) for g in glyphs if g.line_index in indexes
        ]
        carets = member_carets.get(member, {})
        logical = member_bounds.get(member, QRectF())
    ink = QRectF()
    for glyph in glyphs:
        ink = ink.united(
            glyph.transform.mapRect(QFontMetricsF(glyph.font).tightBoundingRect(glyph.text))
        )
    if not ink.isEmpty() and not frame.contains(ink):
        diagnostics.append("ink_overflow")
    return JapaneseLayoutResult(
        tuple(lines),
        tuple(glyphs),
        ink,
        logical,
        frame,
        em,
        tuple(dict.fromkeys(diagnostics)),
        carets,
    )


def _options(
    payload: Mapping[str, Any], style: Mapping[str, Any], flow: str, override=None
) -> dict[str, Any]:
    options = dict(override) if override is not None else typography_from_payload(payload)
    rotate = bool(style.get("rotate_half_width", payload.get("rotate_half_width", False)))
    if not options["enabled"]:
        options.update(
            em_advance=False,
            punctuation_spacing="full",
            line_start_opening="full",
            line_end_closing="full",
            latin_spacing_em=0.0,
            first_line_indent_em=0.0,
            line_adjustment="none",
            hanging=False,
        )
    else:
        rotate = options["latin_orientation"] == "sideways"
    options.update(_rotate_half_width=rotate, _vertical_flow=flow)
    options["_wrap_mode"] = Layout.text_wrap_mode_from_payload(
        payload, text_mode=Layout.text_mode_from_payload(payload)
    )
    return options


def _frame(payload: Mapping[str, Any], rect: Any) -> QRectF:
    if isinstance(rect, QRectF):
        return QRectF(rect)
    if rect is not None:
        return QRectF(*rect)
    x, y, right, bottom = Layout.text_bbox_from_payload(payload) or (0.0, 0.0, 100.0, 100.0)
    return QRectF(
        *Layout.text_padded_rect(
            x, y, right - x, bottom - y, Layout.text_padding_from_payload(payload)
        )
    )


def _annotation_lanes(
    atoms: list[Any], em: float, metrics: QFontMetricsF, vertical: bool
) -> tuple[float, float]:
    before = after = 0.0
    for atom in atoms:
        if atom.kind == "warichu":
            excess = em * max(0.0, 2 * atom.special["size_ratio"] + atom.special["gap_em"] - 1)
            after = max(after, excess)
        if atom.ruby is not None:
            ruby_em = font_em(atom.font) if atom.font is not None else em
            ruby_metrics = QFontMetricsF(atom.font) if atom.font is not None else metrics
            extent = (ruby_em if vertical else ruby_metrics.height()) * float(
                atom.ruby["size_ratio"]
            )
            extent += ruby_em * float(atom.ruby["gap_em"])
            if atom.ruby["position"] == "before":
                before = max(before, extent)
            else:
                after = max(after, extent)
    return before, after


def _place_line(
    broken: BrokenLine,
    text: str,
    start: int,
    end: int,
    next_start: int,
    origin: float,
    cross: float,
    em: float,
    placer: GlyphPlacer,
    line_index: int,
) -> tuple[TextLineGeometry, list[PositionedGlyph], dict[int, QRectF], QRectF]:
    offsets = [0.0] * (end - start + 1)
    steps = [0.0] * max(1, end - start)
    glyphs, carets, occupied = [], {}, QRectF()
    for item in broken.positions:
        along = origin + item.offset
        cell = (
            QRectF(cross, along, em, item.advance)
            if placer.vertical
            else QRectF(along, cross, item.advance, em)
        )
        draw = (
            QRectF(cross, along + item.draw_offset, em, item.atom.advance)
            if placer.vertical
            else QRectF(along + item.draw_offset, cross, item.atom.advance, em)
        )
        if item.atom.ruby is not None:
            draw = QRectF(cell)
        placed, insertion = placer.place(item.atom, draw, line_index)
        if item.atom.kind == "base":
            for index in range(item.atom.start, item.atom.end):
                insertion[index] = placer.caret(cell, 0.0)
            insertion[item.atom.end] = placer.caret(cell, item.advance)
        carets.update(insertion)
        for index in range(item.atom.start, item.atom.end + 1):
            caret = insertion.get(index)
            if caret is not None:
                offsets[index - start] = (caret.top() if placer.vertical else caret.left()) - origin
            if index < item.atom.end:
                steps[index - start] = item.advance
        glyphs.extend(placed)
        occupied = occupied.united(cell)
    if not broken.positions:
        occupied = (
            QRectF(cross, origin, em, em) if placer.vertical else QRectF(origin, cross, 1.0, em)
        )
        carets[start] = placer.caret(occupied, 0.0)
    offsets[-1] = broken.extent
    baseline = (
        origin + placer.metrics.ascent()
        if placer.vertical
        else cross + (em - placer.metrics.height()) * 0.5 + placer.metrics.ascent()
    )
    line = TextLineGeometry(
        text[start:end],
        start,
        next_start,
        cross + em * 0.5 if placer.vertical else origin,
        baseline,
        tuple(offsets),
        broken.extent,
        tuple(steps),
        vertical=placer.vertical,
        clusters=grapheme_spans(text[start:end]),
    )
    return line, glyphs, carets, occupied


def _inside_shape(glyphs: list[PositionedGlyph], shape: QPainterPath) -> bool:
    return all(
        shape_contains_rect(
            shape, glyph.transform.mapRect(QFontMetricsF(glyph.font).tightBoundingRect(glyph.text))
        )
        for glyph in glyphs
        if glyph.text.strip()
    )
