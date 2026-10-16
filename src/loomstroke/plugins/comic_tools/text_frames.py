"""Balloon text frames: deterministic sizing + Text Tool object factory.

The balloon's text is a full Text Tool area-text object (content, writing
mode, alignment, spacing, padding — everything the Text Tool persists), not
a label. Sizing is a deterministic, font-metric-free estimate (same width
model as ``mcp.comic_text_layout``) so balloon auto-size behaves identically
in GUI, tests, and headless flows; the actual glyph layout at render time is
done by the existing vector text pipeline.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from loomstroke.plugins.text_tool.common import TextToolSettings, ensure_text_vector_object
from loomstroke.plugins.vector_tools.common import VectorObject

from .model import BALLOON_TEXT_META_KEY, BalloonRecord

_CJK_RE = re.compile("[　-ヿ㐀-鿿豈-﫿＀-￯⺀-⿟]")

#: em-relative advance approximations (mirrors mcp.comic_text_layout)
_CJK_EM = 1.0
_LATIN_EM = 0.58
_SPACE_EM = 0.35
_PUNCT_EM = 0.45
_PUNCT = set(".,:;!?'\"()[]{}-··…")

LINE_HEIGHT_FACTOR = 1.35


@dataclass(frozen=True)
class TextBlockSize:
    """Estimated tight text bounds (px) for given content + style."""

    width: float
    height: float
    line_count: int


def _char_em(ch: str) -> float:
    if ch == " ":
        return _SPACE_EM
    if ch in _PUNCT:
        return _PUNCT_EM
    if _CJK_RE.match(ch):
        return _CJK_EM
    return _LATIN_EM


def _line_width_em(line: str) -> float:
    return sum(_char_em(ch) for ch in line)


def estimate_text_block_size(
    text: str,
    *,
    font_size_pt: float,
    vertical: bool,
    line_height: float = 1.35,
    max_chars_per_line: int = 14,
) -> TextBlockSize:
    """Deterministic bounds for balloon auto-size.

    Horizontal: explicit newlines are kept; long lines wrap at
    ``max_chars_per_line`` CJK-equivalent ems. Vertical (tategaki): the same
    logic transposed — a "line" is a column, height carries the character
    run, width carries the columns.
    """

    em = max(4.0, float(font_size_pt))
    line_gap = max(1.0, float(line_height)) * em
    raw_lines = [line for line in str(text or "").split("\n")]
    if not any(raw_lines):
        raw_lines = [""]

    wrapped: list[str] = []
    limit_em = max(2.0, float(max_chars_per_line))
    for raw in raw_lines:
        if not raw:
            wrapped.append("")
            continue
        current = ""
        current_em = 0.0
        for ch in raw:
            ch_em = _char_em(ch)
            if current and current_em + ch_em > limit_em:
                wrapped.append(current)
                current = ch
                current_em = ch_em
            else:
                current += ch
                current_em += ch_em
        wrapped.append(current)

    line_count = max(1, len(wrapped))
    longest_em = max((_line_width_em(line) for line in wrapped), default=1.0)
    longest_em = max(1.0, longest_em)
    if vertical:
        # columns right-to-left; each column is one wrapped line
        height = longest_em * em
        width = line_count * line_gap
        return TextBlockSize(width=width, height=height, line_count=line_count)
    width = longest_em * em
    height = line_count * line_gap
    return TextBlockSize(width=width, height=height, line_count=line_count)


_BODY_SHAPE_FACTOR = {
    # ellipse-ish bodies need extra room so the inscribed text rect fits
    "speech_round": math.sqrt(2.0),
    "speech_cloud": math.sqrt(2.0) * 1.05,
    "spiky_balloon": math.sqrt(2.0) * 1.1,
    "speech_rect": 1.0,
}

_MIN_BODY_W = 90.0
_MIN_BODY_H = 70.0


def autosize_body_rect(
    record: BalloonRecord,
    text: str,
    *,
    font_size_pt: float,
    vertical: bool,
    line_height: float = 1.35,
    measured_block: TextBlockSize | None = None,
) -> tuple[float, float, float, float]:
    """The balloon body rect for the given text, centered on the current
    body center (Text bounds → padding → balloon size). The tail target is
    untouched by design — it lives in the record, not in this rect."""

    block = measured_block or estimate_text_block_size(
        text,
        font_size_pt=font_size_pt,
        vertical=vertical,
        line_height=line_height,
    )
    factor = _BODY_SHAPE_FACTOR.get(record.kind, 1.0)
    pad = 2.0 * max(0.0, float(record.padding_px))
    width = max(_MIN_BODY_W, block.width * factor + pad)
    height = max(_MIN_BODY_H, block.height * factor + pad)
    if measured_block is not None:
        width = max(_MIN_BODY_W, (block.width + pad) * factor)
        height = max(_MIN_BODY_H, (block.height + pad) * factor)
    x, y, w, h = record.body_rect
    cx = x + w * 0.5
    cy = y + h * 0.5
    return (cx - width * 0.5, cy - height * 0.5, width, height)


def inner_text_rect(
    body_rect: tuple[float, float, float, float],
    kind: str,
    padding_px: float,
) -> tuple[float, float, float, float]:
    """The text frame inscribed in the balloon body."""

    x, y, w, h = body_rect
    factor = _BODY_SHAPE_FACTOR.get(kind, 1.0)
    inner_w = max(8.0, w / factor - 2.0 * padding_px)
    inner_h = max(8.0, h / factor - 2.0 * padding_px)
    cx = x + w * 0.5
    cy = y + h * 0.5
    return (cx - inner_w * 0.5, cy - inner_h * 0.5, inner_w, inner_h)


def balloon_text_rect(record: BalloonRecord) -> tuple[float, float, float, float]:
    """Page rect of the balloon's text frame: the stored relative frame
    (``text_frame_rel``, user-adjustable) or the kind/padding default."""

    if record.text_frame_rel is not None:
        rx, ry, rw, rh = record.text_frame_rel
        bx, by, bw, bh = record.body_rect
        return (bx + rx * bw, by + ry * bh, max(8.0, rw * bw), max(8.0, rh * bh))
    return inner_text_rect(record.body_rect, record.kind, record.padding_px)


def build_balloon_text_object(
    record: BalloonRecord,
    text: str,
    *,
    word_wrap: bool = True,
    font_size_pt: float = 20.0,
    vertical: bool = True,
    vertical_flow: str = "right",
    align: str = "center",
    line_height: float = 1.35,
    tracking: float = 0.0,
    font_family: str = "",
    font_style: str = "",
    color_rgba: tuple[int, int, int, int] = (17, 17, 17, 255),
    padding_top: float = 0.0,
    padding_right: float = 0.0,
    padding_bottom: float = 0.0,
    padding_left: float = 0.0,
    rotate_half_width: bool = False,
    previous_meta: dict | None = None,
) -> VectorObject:
    """Create/refresh the balloon's text-frame vector object."""

    rect = balloon_text_rect(record)
    settings = TextToolSettings(
        word_wrap=bool(word_wrap),
        font_family=font_family,
        font_style=font_style,
        font_size_pt=float(font_size_pt),
        color_rgba=color_rgba,
        vertical=bool(vertical),
        vertical_flow=vertical_flow,
        align=align,
        line_height=float(line_height),
        tracking=float(tracking),
        padding_top=float(padding_top),
        padding_right=float(padding_right),
        padding_bottom=float(padding_bottom),
        padding_left=float(padding_left),
        rotate_half_width=bool(rotate_half_width),
    )
    obj = ensure_text_vector_object(
        object_id=record.text_object_id or None,
        content=str(text or ""),
        rect=rect,
        settings=settings,
        word_wrap=bool(word_wrap),
        previous_meta=previous_meta,
    )
    meta = dict(obj.meta)
    meta[BALLOON_TEXT_META_KEY] = {
        "schema_version": 1,
        "balloon_id": record.balloon_id,
        "panel_id": record.panel_id,
    }
    return VectorObject(id=obj.id, paths=obj.paths, style=obj.style, meta=meta, preview=obj.preview)


__all__ = [
    "LINE_HEIGHT_FACTOR",
    "TextBlockSize",
    "autosize_body_rect",
    "balloon_text_rect",
    "build_balloon_text_object",
    "estimate_text_block_size",
    "inner_text_rect",
]
