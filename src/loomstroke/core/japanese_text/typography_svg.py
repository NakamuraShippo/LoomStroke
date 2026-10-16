"""Export advanced Japanese text as positioned outlines without source metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

from ..vector_text_layout import TextLayoutCalculator as Layout
from .character_color_paint import glyph_color_parts
from .character_styles import color_runs
from .legacy_color_layout import legacy_clip_path
from .typography import has_japanese_layout
from .typography_layout import build_japanese_layout
from .typography_painter import text_rotation_transform


def _path_data(path: QPainterPath) -> str:
    commands = []
    index = 0
    while index < path.elementCount():
        element = path.elementAt(index)
        if element.isMoveTo():
            commands.append(f"M{element.x:.6f},{element.y:.6f}")
        elif element.isLineTo():
            commands.append(f"L{element.x:.6f},{element.y:.6f}")
        elif element.isCurveTo() and index + 2 < path.elementCount():
            control = path.elementAt(index + 1)
            end = path.elementAt(index + 2)
            commands.append(
                f"C{element.x:.6f},{element.y:.6f} "
                f"{control.x:.6f},{control.y:.6f} {end.x:.6f},{end.y:.6f}"
            )
            index += 2
        index += 1
    return " ".join(commands)


def japanese_svg_elements(payload: Mapping[str, Any]) -> list[str]:
    """Preserve glyph placement even when the SVG consumer lacks this font."""
    result = build_japanese_layout(payload)
    transform = text_rotation_transform(payload)
    raw = Layout.text_style_defaults(payload).get("color_rgba", payload.get("color_rgba"))
    try:
        rgba = tuple(max(0, min(255, round(float(v)))) for v in raw[:4])
    except (TypeError, ValueError, OverflowError):
        rgba = (0, 0, 0, 255)
    if len(rgba) != 4:
        rgba = (0, 0, 0, 255)
    colors = color_runs(payload)
    outer_clip = None if has_japanese_layout(payload) else legacy_clip_path(payload, result.frame)
    elements = []
    for glyph in result.glyphs:
        path = QPainterPath()
        path.addText(QPointF(), glyph.font, glyph.text)
        for selected, clip in glyph_color_parts(glyph, colors, rgba):
            piece = path
            if clip is not None:
                clipping = QPainterPath()
                clipping.addRect(clip)
                piece = path.intersected(clipping)
            piece = glyph.transform.map(piece)
            if outer_clip is not None:
                piece = piece.intersected(outer_clip)
            piece = transform.map(piece)
            if piece.isEmpty():
                continue
            color = f"#{selected[0]:02x}{selected[1]:02x}{selected[2]:02x}"
            elements.append(
                f'<path d="{_path_data(piece)}" fill="{color}" '
                f'fill-opacity="{selected[3] / 255.0:.6f}" stroke="none" fill-rule="nonzero" />'
            )
    return elements
