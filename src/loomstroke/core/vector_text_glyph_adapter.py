"""TextLayoutAdapter — Qt text layout to GlyphRunSnapshot (spec §12.2, V5).

The existing ``TextLayoutCalculator`` math stays the layout source of truth
so the direct GPU path structurally matches the raster path: this adapter
re-runs the same line/character position computations that
``draw_horizontal_text_lines`` performs and only adds the glyph-id
resolution (``QRawFont``) that QPainter kept internal.

Stage 1 scope (horizontal point/area text).  Everything else returns
``None`` and keeps the compatibility raster path (spec §12.5):

- vertical text, shape-frame text, object rotation, visual flips
- inline editing preview (caret / selection spans are runtime overlays)
- glyphs missing from the resolved font (Qt's implicit fallback is not
  replicated; id 0 or multi-unit code points trigger object fallback)
- glyphs whose ink would be clipped by the frame (the raster path clips at
  the frame rect; drawing them unclipped would diverge)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from PySide6.QtGui import QFont, QFontMetricsF, QRawFont

from .japanese_text.typography import has_typography
from .vector_render_models import GlyphRunSnapshot, TextObjectSnapshot, stable_signature
from .vector_text_render_service import VectorTextRenderService

logger = logging.getLogger(__name__)


def direct_text_rendering_enabled() -> bool:
    """Opt-in flag for direct GPU text rendering (V5 stage 1).

    Field testing found presenter-side regressions (text vanishing after
    deselect, slow typing) that the offscreen test path does not reproduce,
    so the direct text path stays off unless explicitly enabled for
    debugging.  Off = every text object keeps the compatibility raster path
    (with view-compositing prescale, i.e. the verified C3 behaviour).
    """

    return os.environ.get("LOOMSTROKE_DIRECT_TEXT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# Perf guard: prefix-advance position derivation is O(len^2) per line, so
# pathologically long lines keep the raster path.
_MAX_LINE_CHARS = 300
_MAX_CONTENT_CHARS = 20_000

# Ink is allowed to exceed the frame clip rect by this much before the
# object falls back (QPainter clips at the frame; small AA bleed is fine).
_CLIP_TOLERANCE = 1.5


def _inline_editing_active(inline: dict[str, Any]) -> bool:
    """True while the inline editor shows a caret or a selection span.

    A stale ``inline_preview`` dict with the cursor hidden must not keep the
    object on the raster path forever.
    """

    if inline.get("show_cursor"):
        return True
    try:
        start = int(inline.get("selection_start", -1))
        end = int(inline.get("selection_end", -1))
    except (TypeError, ValueError):
        return False
    return 0 <= start < end


def build_text_object_snapshot(
    object_id: str,
    payload: dict[str, Any],
) -> TextObjectSnapshot | None:
    """Resolve a text payload into a TextObjectSnapshot, or None to fall back."""

    if not direct_text_rendering_enabled():
        return None
    try:
        return _build(object_id, payload)
    except Exception:
        logger.debug("text glyph adapter failed for %r", object_id, exc_info=True)
        return None


def _build(  # noqa: PLR0911, PLR0912, PLR0915
    object_id: str,
    payload: dict[str, Any],
) -> TextObjectSnapshot | None:
    service = VectorTextRenderService

    # --- Stage-1 feature gates (fallback = None) ---
    if has_typography(payload):
        # The optional direct adapter only implements the legacy horizontal
        # subset. Japanese glyph placement belongs to the shared text raster.
        return None
    style_defaults = service.text_style_defaults(payload)
    if bool(style_defaults.get("vertical", payload.get("vertical", False))):
        return None
    if abs(service.text_rotation_from_payload(payload)) > 1e-6:
        return None
    if bool(payload.get("visual_flip_h")) or bool(payload.get("visual_flip_v")):
        return None
    if payload.get("shape_frame"):
        return None
    inline = payload.get("inline_preview")
    if isinstance(inline, dict) and _inline_editing_active(inline):
        return None

    frame = service.text_bbox_from_payload(payload)
    if frame is None:
        return None
    content = service.text_content_from_payload(payload)
    if len(content) > _MAX_CONTENT_CHARS:
        return None

    text_mode = service.text_mode_from_payload(payload)
    wrap_mode = service.text_wrap_mode_from_payload(payload, text_mode=text_mode)
    if wrap_mode == "shape":
        return None

    align = service.text_align_from_payload(payload)
    if align not in {"left", "center", "right", "justify"}:
        align = "left"
    line_height = max(0.5, min(4.0, service.text_line_height_from_payload(payload)))
    tracking = service.text_tracking_from_payload(payload, style_defaults)
    tracking_runs = service.text_range_runs_from_payload(payload, "tracking_runs")
    line_height_runs = service.text_range_runs_from_payload(payload, "line_height_runs")
    padding = service.text_padding_from_payload(payload)
    color = service.parse_rgba(style_defaults.get("color_rgba", payload.get("color_rgba")))
    color_rgba = (color.red(), color.green(), color.blue(), color.alpha())

    font = service.make_text_font(payload)
    # Per-char tracking mode resets font letter spacing (mirrors
    # draw_horizontal_text_lines) so spacing is not double-counted.
    if tracking_runs:
        font = QFont(font)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
    metrics = QFontMetricsF(font)
    raw_font = QRawFont.fromFont(font)
    if not raw_font.isValid():
        return None
    font_resource_id = f"{raw_font.familyName()}|{raw_font.styleName()}|{raw_font.pixelSize():.3f}"
    if tracking_runs:
        font_resource_id += "|ls0"

    # --- Layout (identical math to draw_horizontal_text_lines) ---
    frame_x0, frame_y0, frame_x1, frame_y1 = frame
    draw_w = max(1.0, frame_x1 - frame_x0)
    draw_h = max(1.0, frame_y1 - frame_y0)
    pad_x, pad_y, pad_w, pad_h = service.text_padded_rect(
        frame_x0, frame_y0, draw_w, draw_h, padding
    )

    if wrap_mode == "none":
        lines = content.splitlines() or [content]
    else:
        lines = service.wrap_text_lines(
            content, metrics, max(1.0, pad_w - 2.0), wrap_mode=wrap_mode
        )
    if any(len(line) > _MAX_LINE_CHARS for line in lines):
        return None

    ascent = metrics.ascent()
    base_line_spacing = metrics.lineSpacing()
    line_step = base_line_spacing * line_height
    baseline = pad_y + ascent + 2.0
    bottom = pad_y + pad_h - 2.0

    line_offsets, final_offset = service.horizontal_text_line_offsets(content, lines, wrap_mode)
    line_y_offsets = service.horizontal_text_line_y_offsets(
        lines=lines,
        line_offsets=line_offsets,
        final_offset=final_offset,
        line_step=line_step,
        line_height_runs=line_height_runs,
        base_line_spacing=base_line_spacing,
        line_height=line_height,
    )
    has_per_char = bool(tracking_runs)
    is_justify = align == "justify"

    glyph_ids: list[int] = []
    positions: list[tuple[float, float]] = []
    advances: list[float] = []
    cluster_map: list[int] = []
    glyph_index_cache: dict[str, int] = {}
    clip_x0 = frame_x0 - _CLIP_TOLERANCE
    clip_y0 = frame_y0 - _CLIP_TOLERANCE
    clip_x1 = frame_x1 + _CLIP_TOLERANCE
    clip_y1 = frame_y1 + _CLIP_TOLERANCE

    for idx, line in enumerate(lines):
        draw_line = line if line else " "
        lo = line_offsets[idx] if idx < len(line_offsets) else final_offset
        line_width = service.horizontal_text_total_width(
            draw_line,
            lo,
            metrics=metrics,
            tracking=tracking,
            tracking_runs=tracking_runs,
        )
        justify_extra = service.horizontal_text_justify_extra(
            is_justify=is_justify,
            line_index=idx,
            line_count=len(lines),
            line_text=draw_line,
            line_width=line_width,
            available_width=pad_w - 2.0,
        )
        draw_x = service.horizontal_text_aligned_x(
            x=pad_x,
            width=pad_w,
            line_width=line_width,
            align=align,
        )
        draw_y = baseline + line_y_offsets[idx]
        if draw_y > bottom:
            break

        use_per_char = has_per_char or justify_extra > 0
        char_xs: list[float] | None = None
        if use_per_char:
            char_xs = service.horizontal_text_char_x_offsets(
                draw_line,
                lo,
                metrics=metrics,
                tracking=tracking,
                tracking_runs=tracking_runs,
                extra=justify_extra,
            )

        for ci, ch in enumerate(draw_line):
            if ch.isspace():
                continue
            if char_xs is not None:
                pen_x = draw_x + char_xs[ci]
            else:
                # Kerning-aware prefix advance mirrors what one drawText of
                # the whole line produces.
                pen_x = draw_x + metrics.horizontalAdvance(draw_line[:ci])

            glyph_id = glyph_index_cache.get(ch)
            if glyph_id is None:
                indexes = raw_font.glyphIndexesForString(ch)
                if len(indexes) != 1 or int(indexes[0]) == 0:
                    # Missing glyph or non-BMP code point: Qt would font-
                    # substitute; the direct path cannot yet, so fall back.
                    return None
                glyph_id = int(indexes[0])
                glyph_index_cache[ch] = glyph_id

            ink = raw_font.boundingRect(glyph_id)
            ink_x0 = pen_x + ink.x()
            ink_y0 = draw_y + ink.y()
            if (
                ink_x0 < clip_x0
                or ink_y0 < clip_y0
                or ink_x0 + ink.width() > clip_x1
                or ink_y0 + ink.height() > clip_y1
            ):
                # The raster path clips this ink at the frame; keep parity
                # by falling back instead of drawing it unclipped.
                return None

            glyph_ids.append(glyph_id)
            positions.append((float(pen_x), float(draw_y)))
            advances.append(float(metrics.horizontalAdvance(ch)))
            cluster_map.append(lo + ci)

    run = GlyphRunSnapshot(
        font_resource_id=font_resource_id,
        font_pixel_size=float(raw_font.pixelSize()),
        glyph_ids=tuple(glyph_ids),
        positions=tuple(positions),
        advances=tuple(advances),
        rotations=tuple(0.0 for _ in glyph_ids),
        cluster_map=tuple(cluster_map),
    )
    layout_version = stable_signature(
        (
            content,
            font_resource_id,
            float(raw_font.pixelSize()),
            align,
            float(line_height),
            float(tracking),
            tuple(tuple(sorted(r.items())) for r in (tracking_runs or [])),
            tuple(tuple(sorted(r.items())) for r in (line_height_runs or [])),
            tuple(float(v) for v in padding),
            tuple(float(v) for v in frame),
            wrap_mode,
            color_rgba,
        )
    )
    return TextObjectSnapshot(
        object_id=object_id,
        font_family=raw_font.familyName(),
        font_style=raw_font.styleName(),
        font_pixel_size=float(raw_font.pixelSize()),
        color_rgba=color_rgba,
        frame=(float(frame_x0), float(frame_y0), float(frame_x1), float(frame_y1)),
        glyph_runs=(run,) if glyph_ids else (),
        layout_version=layout_version,
        glyph_resource_version=stable_signature(font_resource_id),
    )


__all__ = ("build_text_object_snapshot", "direct_text_rendering_enabled")
