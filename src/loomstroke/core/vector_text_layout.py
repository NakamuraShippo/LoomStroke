"""Text layout calculations for vector text rendering."""

from __future__ import annotations

import base64
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from typing import Any, cast

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF, QImage, QPainterPath

from loomstroke.core.japanese_text import (
    JapaneseLineBreaker,
    KinsokuPolicy,
    TextIssue,
    TextLayoutValidator,
    TextLineSpan,
)

from .missing_font_style import preserve_missing_font_style
from .vertical_glyph_fallback import glyph_draw_spec, is_center_rotated_char

logger = logging.getLogger(__name__)


class TextLayoutCalculator:
    """Pure layout and payload helpers for vector text."""

    @staticmethod
    def extract_text_payload(vec: Any) -> dict[str, Any] | None:
        meta = getattr(vec, "meta", None)
        if not isinstance(meta, dict):
            return None
        payload = meta.get("text_tool")
        if not isinstance(payload, dict):
            return None
        if str(payload.get("object_type", "text")).lower() != "text":
            return None
        return payload

    @staticmethod
    def make_text_font(payload: Mapping[str, Any]) -> QFont:
        style_defaults = VectorTextRenderService.text_style_defaults(payload)
        family = str(style_defaults.get("font_family", payload.get("font_family", ""))).strip()
        style = str(style_defaults.get("font_style", payload.get("font_style", ""))).strip()
        try:
            size_pt = float(style_defaults.get("font_size_pt", payload.get("font_size_pt", 24.0)))
        except (TypeError, ValueError):
            size_pt = 24.0
        size_pt = max(1.0, min(512.0, size_pt))

        font = QFont(family) if family else QFont()

        if family and style:
            try:
                candidate = QFontDatabase.font(family, style, round(size_pt))
                if candidate.family():
                    font = candidate
            except Exception:
                logger.debug("Failed to resolve font style: %s / %s", family, style)

        tracking = VectorTextRenderService.text_tracking_from_payload(payload, style_defaults)

        font.setPointSizeF(size_pt)
        preserve_missing_font_style(font, family, style)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking)
        return font

    @staticmethod
    def text_style_defaults(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        style_map = payload.get("style_map")
        if isinstance(style_map, Mapping):
            default_style = style_map.get("default")
            if isinstance(default_style, Mapping):
                return default_style
        return {}

    @staticmethod
    def text_paragraph_defaults(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        document = payload.get("document")
        if isinstance(document, Mapping):
            paragraphs = document.get("paragraphs")
            if isinstance(paragraphs, Sequence) and not isinstance(paragraphs, str) and paragraphs:
                first = paragraphs[0]
                if isinstance(first, Mapping):
                    return first
        return {}

    @staticmethod
    def text_content_from_payload(payload: Mapping[str, Any]) -> str:
        document = payload.get("document")
        if isinstance(document, Mapping):
            text = document.get("text")
            if isinstance(text, str):
                return text
            if text is not None:
                return str(text)
        return str(payload.get("content", "") or "")

    @staticmethod
    def text_mode_from_payload(payload: Mapping[str, Any]) -> str:
        mode = str(payload.get("text_mode", "")).strip().lower()
        if mode in {"point", "area"}:
            return mode
        auto_size = payload.get("auto_size")
        if auto_size is not None:
            return "point" if bool(auto_size) else "area"
        return "point"

    @staticmethod
    def text_align_from_payload(payload: Mapping[str, Any]) -> str:
        paragraph = VectorTextRenderService.text_paragraph_defaults(payload)
        align = paragraph.get("align", payload.get("align", "left"))
        return str(align).strip().lower() or "left"

    @staticmethod
    def text_line_height_from_payload(payload: Mapping[str, Any]) -> float:
        paragraph = VectorTextRenderService.text_paragraph_defaults(payload)
        line_height_raw = paragraph.get("line_height", payload.get("line_height", 1.0))
        try:
            value = float(line_height_raw)
        except (TypeError, ValueError):
            value = 1.0
        return max(0.5, min(4.0, value))

    @staticmethod
    def text_tracking_from_payload(
        payload: Mapping[str, Any],
        style_defaults: Mapping[str, Any] | None = None,
    ) -> float:
        style_values = (
            style_defaults
            if style_defaults is not None
            else VectorTextRenderService.text_style_defaults(payload)
        )
        try:
            tracking = float(style_values.get("tracking", payload.get("tracking", 0.0)))
        except (TypeError, ValueError):
            tracking = 0.0
        return max(-64.0, min(128.0, tracking))

    @staticmethod
    def text_range_runs_from_payload(
        payload: Mapping[str, Any],
        key: str,
    ) -> list[dict[str, Any]] | None:
        raw_runs = payload.get(key)
        if not isinstance(raw_runs, (list, tuple)) or not raw_runs:
            return None
        runs = [
            cast("dict[str, Any]", run)
            for run in raw_runs
            if isinstance(run, Mapping)
            and isinstance(run.get("start"), (int, float))
            and isinstance(run.get("end"), (int, float))
        ]
        return runs or None

    @staticmethod
    def text_vertical_flow_from_payload(payload: Mapping[str, Any]) -> str:
        paragraph = VectorTextRenderService.text_paragraph_defaults(payload)
        flow_raw = paragraph.get("vertical_flow", payload.get("vertical_flow", "left"))
        flow = str(flow_raw).strip().lower()
        if flow in {"left", "right"}:
            return flow
        return "left"

    @staticmethod
    def text_wrap_mode_from_payload(payload: Mapping[str, Any], *, text_mode: str) -> str:
        layout = payload.get("layout")
        if isinstance(layout, Mapping):
            wrap_mode = str(layout.get("wrap_mode", "")).strip().lower()
            if wrap_mode in {"none", "word", "char", "shape"}:
                return wrap_mode
        word_wrap = payload.get("word_wrap")
        if word_wrap is not None:
            return "word" if bool(word_wrap) else "none"
        if text_mode == "area":
            return "word"
        return "none"

    @staticmethod
    def text_padding_from_payload(
        payload: Mapping[str, Any],
    ) -> tuple[float, float, float, float]:
        layout = payload.get("layout")
        layout_values = layout if isinstance(layout, Mapping) else {}

        def read_pad(side: str) -> float:
            key = f"padding_{side}"
            try:
                return max(0.0, min(256.0, float(layout_values.get(key, payload.get(key, 0.0)))))
            except (TypeError, ValueError):
                return 0.0

        return (
            read_pad("top"),
            read_pad("right"),
            read_pad("bottom"),
            read_pad("left"),
        )

    @staticmethod
    def text_padded_rect(
        x: float,
        y: float,
        width: float,
        height: float,
        padding: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        pad_top, pad_right, pad_bottom, pad_left = padding
        return (
            x + pad_left,
            y + pad_top,
            max(1.0, width - pad_left - pad_right),
            max(1.0, height - pad_top - pad_bottom),
        )

    @staticmethod
    def text_rotation_from_payload(payload: Mapping[str, Any]) -> float:
        transform = payload.get("transform")
        if isinstance(transform, Mapping):
            raw_rotation = transform.get("rotation_deg", payload.get("rotation_deg", 0.0))
        else:
            raw_rotation = payload.get("rotation_deg", 0.0)
        try:
            rotation_deg = float(raw_rotation)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(rotation_deg):
            return 0.0
        return rotation_deg

    @staticmethod
    def horizontal_text_line_offsets(
        content: str,
        lines: Sequence[str],
        wrap_mode: str,
    ) -> tuple[list[int], int]:
        line_offsets: list[int] = []
        offset = 0
        raw_lines = content.splitlines(keepends=True) if wrap_mode == "none" else None
        for idx, line in enumerate(lines):
            line_offsets.append(offset)
            if raw_lines is not None and idx < len(raw_lines):
                offset += len(raw_lines[idx])
            else:
                offset += len(line)
                if wrap_mode != "none" and offset < len(content):
                    # The breaker consumes exactly one break character per
                    # soft / hard break (a space, "\n", or "\r\n").
                    if content.startswith("\r\n", offset):
                        offset += 2
                    elif content[offset] in {" ", "\n", "\r"}:
                        offset += 1
        return line_offsets, offset

    @staticmethod
    def horizontal_text_line_y_offsets(
        *,
        lines: Sequence[str],
        line_offsets: Sequence[int],
        final_offset: int,
        line_step: float,
        line_height_runs: Sequence[Mapping[str, Any]] | None,
        base_line_spacing: float,
        line_height: float,
    ) -> list[float]:
        if not line_height_runs:
            return [idx * line_step for idx in range(len(lines))]

        def line_step_for(lo: int, line_len: int) -> float:
            line_end = lo + line_len
            for lr in line_height_runs:
                lr_s = int(lr["start"])
                lr_e = int(lr["end"])
                if lr_s < line_end and lr_e > lo:
                    return base_line_spacing * float(lr.get("line_height", line_height))
            return line_step

        line_y_offsets: list[float] = []
        accum = 0.0
        for li, line in enumerate(lines):
            line_y_offsets.append(accum)
            lo = line_offsets[li] if li < len(line_offsets) else final_offset
            accum += line_step_for(lo, len(line))
        return line_y_offsets

    @staticmethod
    def horizontal_text_tracking_at(
        global_index: int,
        *,
        tracking: float,
        tracking_runs: Sequence[Mapping[str, Any]] | None,
    ) -> float:
        if not tracking_runs:
            return tracking
        for tr in tracking_runs:
            if int(tr["start"]) <= global_index < int(tr["end"]):
                return float(tr.get("tracking", tracking))
        return tracking

    @staticmethod
    def horizontal_text_char_x_offsets(
        line_text: str,
        line_start_offset: int,
        *,
        metrics: QFontMetricsF,
        tracking: float,
        tracking_runs: Sequence[Mapping[str, Any]] | None,
        extra: float = 0.0,
    ) -> list[float]:
        offsets: list[float] = []
        cx = 0.0
        for ci, ch in enumerate(line_text):
            offsets.append(cx)
            char_w = metrics.horizontalAdvance(ch)
            t = VectorTextRenderService.horizontal_text_tracking_at(
                line_start_offset + ci,
                tracking=tracking,
                tracking_runs=tracking_runs,
            )
            cx += char_w + t + extra
        return offsets

    @staticmethod
    def horizontal_text_total_width(
        line_text: str,
        line_start_offset: int,
        *,
        metrics: QFontMetricsF,
        tracking: float,
        tracking_runs: Sequence[Mapping[str, Any]] | None,
    ) -> float:
        if not tracking_runs:
            return metrics.horizontalAdvance(line_text)
        if not line_text:
            return 0.0
        cx = 0.0
        for ci, ch in enumerate(line_text):
            char_w = metrics.horizontalAdvance(ch)
            t = VectorTextRenderService.horizontal_text_tracking_at(
                line_start_offset + ci,
                tracking=tracking,
                tracking_runs=tracking_runs,
            )
            cx += char_w + t
        trailing = VectorTextRenderService.horizontal_text_tracking_at(
            line_start_offset + len(line_text) - 1,
            tracking=tracking,
            tracking_runs=tracking_runs,
        )
        return max(0.0, cx - trailing)

    @staticmethod
    def horizontal_text_justify_extra(
        *,
        is_justify: bool,
        line_index: int,
        line_count: int,
        line_text: str,
        line_width: float,
        available_width: float,
    ) -> float:
        if not (is_justify and line_index < line_count - 1 and len(line_text.strip()) > 1):
            return 0.0
        if available_width <= line_width:
            return 0.0
        return (available_width - line_width) / max(1, len(line_text) - 1)

    @staticmethod
    def horizontal_text_aligned_x(
        *,
        x: float,
        width: float,
        line_width: float,
        align: str,
    ) -> float:
        if align == "center":
            return x + (width - line_width) * 0.5
        if align == "right":
            return x + (width - line_width)
        return x + 1.0

    @staticmethod
    def horizontal_text_caret_line_position(
        cursor_pos: int,
        *,
        line_offsets: Sequence[int],
        lines: Sequence[str],
    ) -> tuple[int, int] | None:
        caret_line = 0
        caret_col = cursor_pos
        for li in range(len(line_offsets)):
            if li + 1 < len(line_offsets) and cursor_pos >= line_offsets[li + 1]:
                continue
            caret_line = li
            caret_col = cursor_pos - line_offsets[li]
            break
        if caret_line >= len(lines):
            return None
        return caret_line, min(caret_col, len(lines[caret_line]))

    @staticmethod
    def wrap_text_spans(
        content: str,
        metrics: QFontMetricsF,
        max_width: float,
        *,
        wrap_mode: str = "word",
        policy: KinsokuPolicy | None = None,
    ) -> list[TextLineSpan]:
        """Kinsoku-aware line spans for horizontal text (CT-03).

        ``word`` keeps Latin words whole (a space is the break), ``char``
        breaks anywhere the policy allows; both honour 行頭 / 行末禁則,
        分離禁止 and ぶら下げ.  Per-character advances are measured once.
        """
        width_limit = max(1.0, float(max_width))
        cache: dict[str, float] = {}

        def measure(_index: int, char: str) -> float:
            advance = cache.get(char)
            if advance is None:
                advance = float(metrics.horizontalAdvance(char))
                cache[char] = advance
            return advance

        return JapaneseLineBreaker().break_text(
            content,
            measure,
            width_limit,
            policy=policy or KinsokuPolicy.standard(),
            wrap_mode=wrap_mode,
        )

    @staticmethod
    def wrap_text_lines(
        content: str,
        metrics: QFontMetricsF,
        max_width: float,
        *,
        wrap_mode: str = "word",
        policy: KinsokuPolicy | None = None,
    ) -> list[str]:
        spans = TextLayoutCalculator.wrap_text_spans(
            content, metrics, max_width, wrap_mode=wrap_mode, policy=policy
        )
        return [span.text(content) for span in spans] or [""]

    @staticmethod
    def vertical_text_column_spans(
        content: str,
        *,
        height: float,
        ascent: float,
        base_char_step: float,
        tracking: float = 0.0,
        tracking_runs: list[dict[str, Any]] | None = None,
        wrap_mode: str = "none",
        policy: KinsokuPolicy | None = None,
    ) -> list[TextLineSpan]:
        """Column spans for vertical text (CT-03): the kinsoku breaker with the
        per-character step as the measure and the frame height as the limit.
        The limit mirrors ``draw_vertical_text_lines``: the first glyph sits at
        ``ascent + 2`` and a glyph draws while its baseline stays inside ``h``."""
        if not content:
            return [TextLineSpan(0, 0, 0, hard=True)]

        def measure(index: int, _char: str) -> float:
            step = float(base_char_step)
            if tracking_runs:
                for run in tracking_runs:
                    if int(run["start"]) <= index < int(run["end"]):
                        return step + float(run.get("tracking", tracking))
            return step + float(tracking)

        limit = max(1.0, float(height) - 2.0 - float(ascent) + float(base_char_step))
        return JapaneseLineBreaker().break_text(
            content,
            measure,
            limit,
            policy=policy or KinsokuPolicy.standard(),
            wrap_mode=wrap_mode,
        )

    @staticmethod
    def horizontal_text_line_capacity(height: float, ascent: float, line_step: float) -> int:
        """How many baselines fit the frame the way ``draw_horizontal_text_lines`` draws."""
        step = float(line_step) if line_step and line_step > 0 else 1.0
        usable = float(height) - 4.0 - float(ascent)
        if usable < 0:
            return 0
        return int(usable // step) + 1

    @staticmethod
    def horizontal_text_layout_issues(
        content: str,
        metrics: QFontMetricsF,
        *,
        width: float,
        height: float,
        line_step: float,
        wrap_mode: str = "word",
        policy: KinsokuPolicy | None = None,
    ) -> tuple[TextIssue, ...]:
        """Overflow / forced-break diagnostics for an area text (CT-03 段階 2)."""
        limit = max(1.0, float(width) - 2.0)
        spans = TextLayoutCalculator.wrap_text_spans(
            content, metrics, limit, wrap_mode=wrap_mode, policy=policy
        )
        capacity = TextLayoutCalculator.horizontal_text_line_capacity(
            height, metrics.ascent(), line_step
        )
        return TextLayoutValidator().issues(
            content,
            spans,
            measure=lambda _i, ch: float(metrics.horizontalAdvance(ch)),
            max_extent=limit,
            line_capacity=capacity,
        )

    @staticmethod
    def fit_shape_line_text(
        text: str,
        metrics: QFontMetricsF,
        max_width: float,
    ) -> tuple[str, int, bool]:
        if not text:
            return ("", 0, False)
        if text[0] == "\n":
            return ("", 1, True)

        best_len = 0
        last_space = -1
        idx = 0
        while idx < len(text):
            ch = text[idx]
            if ch == "\n":
                if best_len <= 0:
                    return ("", 1, True)
                draw_text = text[:best_len].rstrip()
                consumed = idx + 1
                return (draw_text or text[:best_len], consumed, True)
            candidate_len = idx + 1
            if metrics.horizontalAdvance(text[:candidate_len]) <= max_width:
                best_len = candidate_len
                if ch.isspace():
                    last_space = candidate_len
                idx += 1
                continue
            break

        if best_len <= 0:
            return (text[:1], 1, False)

        cut = best_len
        if 0 < last_space < best_len:
            cut = last_space
        draw_text = text[:cut].rstrip()
        consumed = cut
        while consumed < len(text) and text[consumed] == " ":
            consumed += 1
        return (draw_text or text[:best_len], consumed, False)

    @staticmethod
    def resolve_shape_handle_point(
        *,
        raw_x: float,
        raw_y: float,
        anchor_x: float,
        anchor_y: float,
        frame_x: float,
        frame_y: float,
        frame_w: float,
        frame_h: float,
        handle_space: str,
    ) -> tuple[float, float]:
        relative_x = anchor_x + (raw_x * frame_w)
        relative_y = anchor_y + (raw_y * frame_h)
        legacy_x = raw_x * frame_w
        legacy_y = raw_y * frame_h

        if handle_space == "anchor_delta":
            return (relative_x, relative_y)
        if handle_space in {"", "legacy_world_div"}:
            return (legacy_x, legacy_y)

        min_x = frame_x - frame_w
        max_x = frame_x + (frame_w * 2.0)
        min_y = frame_y - frame_h
        max_y = frame_y + (frame_h * 2.0)

        def _penalty(px: float, py: float) -> float:
            dx = 0.0
            if px < min_x:
                dx = min_x - px
            elif px > max_x:
                dx = px - max_x
            dy = 0.0
            if py < min_y:
                dy = min_y - py
            elif py > max_y:
                dy = py - max_y
            return dx + dy

        if _penalty(legacy_x, legacy_y) + 1e-6 < _penalty(relative_x, relative_y):
            return (legacy_x, legacy_y)
        return (relative_x, relative_y)

    @staticmethod
    def text_shape_path_from_payload(
        payload: Mapping[str, Any],
        *,
        x: float,
        y: float,
        w: float,
        h: float,
        contour_factory: Callable[[QImage, int, int], Any],
    ) -> QPainterPath | Any | None:
        if w <= 1e-6 or h <= 1e-6:
            return None
        shape_frame = payload.get("shape_frame")
        if not isinstance(shape_frame, Mapping):
            return None

        try:
            mask_shape = VectorTextRenderService.text_mask_shape_from_png(
                shape_frame,
                x=x,
                y=y,
                w=w,
                h=h,
                contour_factory=contour_factory,
            )
            if mask_shape is not None:
                return mask_shape
        except Exception:
            logger.debug("Failed to decode mask_png shape", exc_info=True)

        return VectorTextRenderService.text_shape_path_from_paths(
            shape_frame,
            x=x,
            y=y,
            w=w,
            h=h,
        )

    @staticmethod
    def text_shape_path_from_paths(  # noqa: PLR0912, PLR0915
        shape_frame: Mapping[str, Any],
        *,
        x: float,
        y: float,
        w: float,
        h: float,
    ) -> QPainterPath | None:
        raw_paths = shape_frame.get("paths")
        if not isinstance(raw_paths, Sequence):
            return None
        handle_space = str(shape_frame.get("handle_space", "")).strip().lower()

        composed = QPainterPath()
        has_path = False
        for raw_path in raw_paths:
            if not isinstance(raw_path, Mapping):
                continue
            raw_anchors = raw_path.get("anchors")
            if not isinstance(raw_anchors, Sequence):
                continue
            anchors: list[dict[str, Any]] = [
                cast("dict[str, Any]", a) for a in raw_anchors if isinstance(a, Mapping)
            ]
            if not anchors:
                continue
            first_pos = anchors[0].get("pos")
            if not (isinstance(first_pos, Sequence) and len(first_pos) >= 2):
                continue
            try:
                first_x = x + (float(first_pos[0]) * w)
                first_y = y + (float(first_pos[1]) * h)
            except (TypeError, ValueError):
                continue

            path = QPainterPath()
            path.moveTo(first_x, first_y)
            closed = bool(raw_path.get("closed", False))
            segment_count = len(anchors) if closed and len(anchors) > 1 else len(anchors) - 1
            for idx in range(segment_count):
                current = anchors[idx]
                nxt = anchors[(idx + 1) % len(anchors)]
                current_pos = current.get("pos")
                next_pos = nxt.get("pos")
                if not (
                    isinstance(current_pos, Sequence)
                    and len(current_pos) >= 2
                    and isinstance(next_pos, Sequence)
                    and len(next_pos) >= 2
                ):
                    continue
                try:
                    ax = x + (float(current_pos[0]) * w)
                    ay = y + (float(current_pos[1]) * h)
                    bx = x + (float(next_pos[0]) * w)
                    by = y + (float(next_pos[1]) * h)
                except (TypeError, ValueError):
                    continue
                h_out = current.get("h_out")
                h_in = nxt.get("h_in")
                if isinstance(h_out, Sequence) and len(h_out) >= 2:
                    try:
                        c1x, c1y = VectorTextRenderService.resolve_shape_handle_point(
                            raw_x=float(h_out[0]),
                            raw_y=float(h_out[1]),
                            anchor_x=ax,
                            anchor_y=ay,
                            frame_x=x,
                            frame_y=y,
                            frame_w=w,
                            frame_h=h,
                            handle_space=handle_space,
                        )
                    except (TypeError, ValueError):
                        c1x, c1y = ax, ay
                else:
                    c1x, c1y = ax, ay
                if isinstance(h_in, Sequence) and len(h_in) >= 2:
                    try:
                        c2x, c2y = VectorTextRenderService.resolve_shape_handle_point(
                            raw_x=float(h_in[0]),
                            raw_y=float(h_in[1]),
                            anchor_x=bx,
                            anchor_y=by,
                            frame_x=x,
                            frame_y=y,
                            frame_w=w,
                            frame_h=h,
                            handle_space=handle_space,
                        )
                    except (TypeError, ValueError):
                        c2x, c2y = bx, by
                else:
                    c2x, c2y = bx, by
                has_handles = (isinstance(h_out, Sequence) and len(h_out) >= 2) or (
                    isinstance(h_in, Sequence) and len(h_in) >= 2
                )
                if has_handles:
                    path.cubicTo(c1x, c1y, c2x, c2y, bx, by)
                else:
                    path.lineTo(bx, by)

            if bool(raw_path.get("closed", False)):
                path.closeSubpath()
            composed.addPath(path)
            has_path = True

        if not has_path:
            return None
        return composed

    @staticmethod
    def text_mask_shape_from_png(
        shape_frame: Mapping[str, Any],
        *,
        x: float,
        y: float,
        w: float,
        h: float,
        contour_factory: Callable[[QImage, int, int], Any],
    ) -> Any | None:
        mask_png = shape_frame.get("mask_png")
        if not isinstance(mask_png, str) or not mask_png:
            return None

        raw = base64.b64decode(mask_png)
        mask_img = QImage()
        mask_img.loadFromData(raw, cast("Any", "PNG"))
        if mask_img.isNull():
            return None

        if mask_img.width() != int(w) or mask_img.height() != int(h):
            mask_img = mask_img.scaled(
                max(1, int(w)),
                max(1, int(h)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return contour_factory(mask_img, int(x), int(y))

    @staticmethod
    def shape_segments_at_y(
        shape_path: Any,
        x0: float,
        x1: float,
        y: float,
    ) -> list[tuple[float, float]]:
        query = getattr(shape_path, "segments_at_y", None)
        if callable(query):
            return query(x0, x1, y)
        left = math.floor(min(x0, x1))
        right = math.ceil(max(x0, x1))
        if right <= left:
            return []
        segments: list[tuple[float, float]] = []
        in_segment = False
        start = float(left)
        for xi in range(left, right + 1):
            point = QPointF(float(xi) + 0.5, y)
            inside = bool(shape_path.contains(point))
            if inside and not in_segment:
                start = float(xi)
                in_segment = True
            elif (not inside) and in_segment:
                segments.append((start, float(xi)))
                in_segment = False
        if in_segment:
            segments.append((start, float(right)))
        return [seg for seg in segments if (seg[1] - seg[0]) > 1.5]

    @staticmethod
    def shape_caret_position(  # noqa: PLR0911, PLR0912, PLR0915
        *,
        content: str,
        cursor_pos: int,
        metrics: QFontMetricsF,
        x: float,
        y: float,
        w: float,
        h: float,
        line_height: float,
        shape_path: Any,
    ) -> tuple[float, float] | None:
        text = content.replace("\r\n", "\n").replace("\r", "\n")
        try:
            target = int(cursor_pos)
        except (TypeError, ValueError):
            return None
        max_len = len(text)
        target = max(0, min(max_len, target))

        ascent = metrics.ascent()
        line_step = metrics.lineSpacing() * line_height
        baseline = y + ascent + 2.0
        bottom = y + h - 2.0

        cursor = 0
        fallback_x = x + 1.0
        fallback_base = baseline

        while baseline <= bottom:
            probe_y = baseline - (ascent * 0.55)
            segments = VectorTextRenderService.shape_segments_at_y(
                shape_path,
                x + 1.0,
                x + w - 1.0,
                probe_y,
            )
            if segments:
                fallback_x = segments[0][0] + 1.0
                fallback_base = baseline
                if target == cursor:
                    return (fallback_x, fallback_base)

            move_to_next_line = False
            for seg_x0, seg_x1 in segments:
                seg_start_x = seg_x0 + 1.0
                if target == cursor:
                    return (seg_start_x, baseline)
                while cursor < max_len and text[cursor] == " ":
                    if target == cursor:
                        return (seg_start_x, baseline)
                    cursor += 1
                if target == cursor:
                    return (seg_start_x, baseline)
                if cursor >= max_len:
                    return (
                        (seg_start_x, baseline) if target >= cursor else (fallback_x, fallback_base)
                    )
                if text[cursor] == "\n":
                    if target == cursor:
                        return (seg_start_x, baseline)
                    cursor += 1
                    move_to_next_line = True
                    break
                available = max(0.0, seg_x1 - seg_x0 - 2.0)
                if available <= 1.0:
                    continue
                draw_text, consumed, forced_newline = VectorTextRenderService.fit_shape_line_text(
                    text[cursor:],
                    metrics,
                    available,
                )
                if consumed <= 0:
                    continue
                if target >= cursor and target <= (cursor + consumed):
                    relative = target - cursor
                    relative_visible = max(0, min(relative, len(draw_text)))
                    prefix = draw_text[:relative_visible]
                    caret_x = seg_start_x + metrics.horizontalAdvance(prefix)
                    return (caret_x, baseline)
                fallback_x = (
                    seg_start_x + metrics.horizontalAdvance(draw_text) if draw_text else seg_start_x
                )
                fallback_base = baseline
                cursor += consumed
                if forced_newline:
                    move_to_next_line = True
                    break

            if cursor >= max_len and target >= cursor:
                return (fallback_x, fallback_base)
            baseline += line_step
            if move_to_next_line:
                continue

        return (fallback_x, min(fallback_base, y + h - 1.0))

    @staticmethod
    def font_supports_char(metrics: QFontMetricsF, ch: str) -> bool:
        if not ch:
            return False
        codepoint = ord(ch[0])
        with suppress(Exception):
            in_font_ucs4 = getattr(metrics, "inFontUcs4", None)
            if callable(in_font_ucs4):
                return bool(in_font_ucs4(codepoint))
        with suppress(Exception):
            in_font = getattr(metrics, "inFont", None)
            if callable(in_font):
                return bool(in_font(ch[0]))
        return True

    @staticmethod
    def is_vertical_center_rotated_char(ch: str) -> bool:
        return is_center_rotated_char(ch)

    @staticmethod
    def vertical_glyph_draw_spec(  # noqa: PLR0911
        ch: str,
        *,
        metrics: QFontMetricsF,
        column_step: float,
        char_step: float,
        vertical_flow: str,
        rotate_half_width: bool = False,
    ) -> tuple[str, float, float, float]:
        return glyph_draw_spec(
            ch,
            metrics=metrics,
            column_step=column_step,
            char_step=char_step,
            vertical_flow=vertical_flow,
            rotate_half_width=rotate_half_width,
            supports_char=lambda glyph: VectorTextRenderService.font_supports_char(metrics, glyph),
        )

    @staticmethod
    def vertical_reference_center_offset(metrics: QFontMetricsF) -> tuple[float, float]:
        with suppress(Exception):
            ref_bounds = metrics.tightBoundingRect("日")
            if ref_bounds is not None and not ref_bounds.isEmpty():
                center = ref_bounds.center()
                return (float(center.x()), float(center.y()))
        return (0.0, -(metrics.ascent() * 0.5))

    @staticmethod
    def text_inline_cursor_from_payload(payload: Mapping[str, Any]) -> int | None:
        inline_preview = payload.get("inline_preview")
        if not isinstance(inline_preview, Mapping):
            return None
        if not bool(inline_preview.get("show_cursor", False)):
            return None
        try:
            cursor_pos = int(inline_preview.get("cursor_pos", 0))
        except (TypeError, ValueError):
            return None
        return max(0, cursor_pos)

    @staticmethod
    def text_inline_selection_from_payload(payload: Mapping[str, Any]) -> tuple[int, int] | None:
        inline_preview = payload.get("inline_preview")
        if not isinstance(inline_preview, Mapping):
            return None
        if not bool(inline_preview.get("show_cursor", False)):
            return None
        try:
            start = int(inline_preview.get("selection_start", -1))
            end = int(inline_preview.get("selection_end", -1))
        except (TypeError, ValueError):
            return None
        if not 0 <= start < end:
            return None
        return (start, end)

    @staticmethod
    def text_inline_cursor_for_content(
        payload: Mapping[str, Any],
        content_length: int,
    ) -> int | None:
        cursor_pos = VectorTextRenderService.text_inline_cursor_from_payload(payload)
        if cursor_pos is None:
            return None
        return min(cursor_pos, max(0, content_length))

    @staticmethod
    def text_inline_selection_for_content(
        payload: Mapping[str, Any],
        content_length: int,
    ) -> tuple[int, int] | None:
        inline_selection = VectorTextRenderService.text_inline_selection_from_payload(payload)
        if inline_selection is None:
            return None
        sel_start, sel_end = inline_selection
        sel_end = min(sel_end, max(0, content_length))
        return (sel_start, sel_end) if sel_start < sel_end else None

    @staticmethod
    def text_bbox_from_payload(
        payload: Mapping[str, Any],
    ) -> tuple[float, float, float, float] | None:
        frame = payload.get("frame")
        if isinstance(frame, Mapping):
            try:
                x = float(frame.get("x", payload.get("x")))
                y = float(frame.get("y", payload.get("y")))
                w = float(frame.get("width", payload.get("width")))
                h = float(frame.get("height", payload.get("height")))
            except (TypeError, ValueError):
                x = y = w = h = float("nan")
            if all(math.isfinite(value) for value in (x, y, w, h)) and w > 0 and h > 0:
                return (x, y, x + w, y + h)
        try:
            x = float(cast("Any", payload.get("x")))
            y = float(cast("Any", payload.get("y")))
            w = float(cast("Any", payload.get("width")))
            h = float(cast("Any", payload.get("height")))
        except (TypeError, ValueError):
            return None
        if not (all(math.isfinite(v) for v in (x, y, w, h)) and w > 0 and h > 0):
            return None
        return (x, y, x + w, y + h)


VectorTextRenderService = TextLayoutCalculator

__all__ = ("TextLayoutCalculator",)
