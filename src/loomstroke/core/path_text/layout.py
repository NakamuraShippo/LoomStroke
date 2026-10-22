"""Map shared shaped glyphs and editing carets onto a bounded path interval."""

from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFontMetricsF

from ..japanese_text.typography_layout_types import JapaneseLayoutResult
from .collision import PathSpacing
from .geometry import PathTrack


def _straight_source(payload, first_line):
    source = deepcopy(dict(payload))
    source.pop("path_text", None)
    source.pop("shape_frame", None)
    source.pop("linked_story", None)
    source["align"] = "left"
    source["vertical"] = False
    if isinstance(source.get("style_map"), dict):
        source["style_map"].setdefault("default", {}).update(vertical=False)
    source["layout"] = dict(source.get("layout") or {}, wrap_mode="char", columns=1)
    document = source.get("document", {})
    for paragraph in document.get("paragraphs", []):
        paragraph.update(align="left")
    source["document"] = dict(document, text=first_line)
    source["content"] = first_line
    return source


def build_path_layout(payload, text, font, rect, compose):
    track = PathTrack.from_payload(payload, rect=rect)
    if track is None:
        return JapaneseLayoutResult(
            (), (), QRectF(), QRectF(), rect, QFontMetricsF(font).height(), ("path_text_invalid",)
        )
    # One contour carries one line. Keep subsequent lines in the saved body;
    # report overflow instead of silently wrapping or overlapping a second lap.
    first_line = text.split("\n", 1)[0]
    source = _straight_source(payload, first_line)
    straight = compose(source, content=first_line, font=font, rect=QRectF(0, 0, 1e8, 1e6))
    baseline = straight.lines[0].baseline if straight.lines else QFontMetricsF(font).ascent()
    initial = straight.carets.get(0, QRectF()).left()
    end = straight.carets.get(len(first_line), QRectF()).left()
    extent = max(0.0, end - initial)
    space = track.interval_length
    align = track.settings.get("align", "left")
    spare = max(0.0, space - extent)
    shift = spare * (0.5 if align == "center" else 1 if align == "right" else 0)
    bodies = [g for g in straight.glyphs if g.role == "base"]
    extra = spare / (len(bodies) - 1) if align == "justify" and len(bodies) > 1 else 0
    positions = {g.start: i * extra for i, g in enumerate(bodies)}
    distance = PathSpacing(track, straight, baseline, initial, shift, positions)
    cutoff = distance.cutoff

    def bend(x, index):
        result = track.frame_at(max(0, min(space, distance(x, index))))
        result.translate(-x, -baseline)
        return result

    glyphs, ink, logical = [], QRectF(), QRectF()
    overflow = len(first_line) != len(text) or extent > space + 1e-6
    for glyph in straight.glyphs:
        if cutoff is not None and glyph.start >= cutoff:
            overflow = True
            continue
        cell = glyph.group_cell if glyph.group_cell is not None else glyph.cell
        if distance(cell.right(), glyph.start) > space + 1e-6 or space <= 1e-6:
            overflow = True
            continue
        transform = bend(cell.center().x(), glyph.start)
        placed = replace(
            glyph,
            transform=glyph.transform * transform,
            cell=transform.mapRect(glyph.cell),
            group_cell=(
                transform.mapRect(glyph.group_cell) if glyph.group_cell is not None else None
            ),
        )
        glyphs.append(placed)
        ink = ink.united(
            placed.transform.mapRect(QFontMetricsF(glyph.font).tightBoundingRect(glyph.text))
        )
        logical = logical.united(placed.cell)
    carets = {
        index: bend(caret.x(), index).mapRect(caret) for index, caret in straight.carets.items()
    }
    fallback = track.frame_at(space).mapRect(
        QRectF(0, -QFontMetricsF(font).ascent(), 1, QFontMetricsF(font).height())
    )
    for index in range(len(first_line), len(text) + 1):
        carets.setdefault(index, fallback)
    if cutoff is not None:
        for index in range(cutoff, len(text) + 1):
            carets[index] = fallback
    if not carets:
        carets[0] = track.frame_at(0).mapRect(
            QRectF(0, -QFontMetricsF(font).ascent(), 1, QFontMetricsF(font).height())
        )
    return replace(
        straight,
        glyphs=tuple(glyphs),
        carets=carets,
        frame=rect,
        ink_bounds=ink,
        logical_bounds=logical,
        diagnostics=straight.diagnostics + (("path_text_overflow",) if overflow else ()),
    )
