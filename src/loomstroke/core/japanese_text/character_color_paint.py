"""Paint shaped glyphs in run colors without reshaping or moving their carets."""

from __future__ import annotations

from itertools import pairwise

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QPolygonF, QRegion, QTransform

from .character_styles import ColorRuns, color_at
from .typography_shaping import run_cursor_offsets


def glyph_color_parts(glyph, runs, default):
    """Return (RGBA, local clip) pieces; annotations inherit their first parent color.

    Latin ligatures and TCY retain the original shaped glyph. A color boundary
    clips the painted glyph at its insertion position, leaving geometry intact.
    """
    if glyph.role not in {"base", "tcy"}:
        yield color_at(runs, glyph.start, default), None
        return
    cuts = {glyph.start, glyph.end}
    overlapping = runs.overlapping(glyph.start, glyph.end) if isinstance(runs, ColorRuns) else runs
    for start, end, _color in overlapping:
        if glyph.start < start < glyph.end:
            cuts.add(start)
        if glyph.start < end < glyph.end:
            cuts.add(end)
    boundaries = sorted(cuts)
    parts = []
    for start, end in pairwise(boundaries):
        color = color_at(runs, start, default)
        if parts and parts[-1][2] == color:
            parts[-1] = (parts[-1][0], end, color)
        else:
            parts.append((start, end, color))
    if len(parts) <= 1:
        yield (parts[0][2] if parts else default), None
        return
    offsets = run_cursor_offsets(glyph.text, glyph.font)
    # The rasterizer can cover pixels just outside metric ink bounds. Only
    # interior caret boundaries divide colors; outer edges must retain that ink.
    metrics = QFontMetricsF(glyph.font)
    margin = max(64.0, metrics.height() * 2)
    ink = metrics.tightBoundingRect(glyph.text).adjusted(-margin, -margin, margin, margin)
    for start, end, color in parts:
        a = offsets[min(len(offsets) - 1, start - glyph.start)]
        b = offsets[min(len(offsets) - 1, end - glyph.start)]
        left, right = min(a, b), max(a, b)
        if start == glyph.start:
            left = min(left, ink.left())
        if end == glyph.end:
            right = max(right, ink.right())
        yield color, QRectF(left, ink.top() - 1, right - left, ink.height() + 2)


def apply_glyph_clip(painter, clip):
    """Partition device pixels once, avoiding overlapping bitmap clip edges."""
    if not hasattr(painter, "deviceTransform"):
        painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
        return
    polygon = painter.deviceTransform().map(QPolygonF(clip))
    region = QRegion(polygon.toPolygon())
    world = painter.worldTransform()
    dpr = painter.device().devicePixelRatioF()
    painter.setWorldTransform(QTransform.fromScale(1.0 / dpr, 1.0 / dpr))
    painter.setClipRegion(region, Qt.ClipOperation.IntersectClip)
    painter.setWorldTransform(world)
