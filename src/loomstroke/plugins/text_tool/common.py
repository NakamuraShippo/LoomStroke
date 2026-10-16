from __future__ import annotations

import logging
import math
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtGui import QFontDatabase

from loomstroke.core.japanese_text.annotations import ANNOTATION_KEYS, shift_annotation_ranges
from loomstroke.core.japanese_text.typography import normalize_typography
from loomstroke.core.text_edit_delta import changed_text_span
from loomstroke.plugins.vector_tools.common import (
    Anchor,
    VectorObject,
    VectorPath,
    VectorStyle,
)

logger = logging.getLogger(__name__)

TEXT_META_KEY = "text_tool"
TEXT_TOOL_ID = "text_tool"
TEXT_TOOL_VERTICAL_ID = "text_tool_vertical"
TEXT_TOOL_IDS = (TEXT_TOOL_ID, TEXT_TOOL_VERTICAL_ID)
TEXT_SCHEMA_VERSION = 2
TEXT_MODE_POINT = "point"
TEXT_MODE_AREA = "area"
TEXT_MODE_VALUES = {TEXT_MODE_POINT, TEXT_MODE_AREA}
VERTICAL_FLOW_LEFT = "left"
VERTICAL_FLOW_RIGHT = "right"
VERTICAL_FLOW_VALUES = {VERTICAL_FLOW_LEFT, VERTICAL_FLOW_RIGHT}

_FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".otc"}
_LOADED_FONT_FILES: set[str] = set()


@dataclass(slots=True)
class TextToolSettings:
    word_wrap: bool = True
    font_family: str = ""
    font_style: str = ""
    font_size_pt: float = 32.0
    color_rgba: tuple[int, int, int, int] = (0, 0, 0, 255)
    vertical: bool = False
    vertical_flow: str = VERTICAL_FLOW_LEFT
    align: str = "left"
    line_height: float = 1.0
    tracking: float = 0.0
    padding_top: float = 0.0
    padding_right: float = 0.0
    padding_bottom: float = 0.0
    padding_left: float = 0.0
    rotate_half_width: bool = False
    typography: dict[str, Any] = field(default_factory=dict)


def copy_text_tool_settings(settings: TextToolSettings) -> TextToolSettings:
    """Return a detached copy of text tool settings."""
    return TextToolSettings(
        word_wrap=bool(settings.word_wrap),
        font_family=str(settings.font_family),
        font_style=str(settings.font_style),
        font_size_pt=float(settings.font_size_pt),
        color_rgba=tuple(settings.color_rgba),
        vertical=bool(settings.vertical),
        vertical_flow=str(settings.vertical_flow),
        align=str(settings.align),
        line_height=float(settings.line_height),
        tracking=float(settings.tracking),
        padding_top=float(settings.padding_top),
        padding_right=float(settings.padding_right),
        padding_bottom=float(settings.padding_bottom),
        padding_left=float(settings.padding_left),
        rotate_half_width=bool(settings.rotate_half_width),
        typography=deepcopy(settings.typography),
    )


def normalize_color_rgba(
    value: Any,
    fallback: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            r = round(float(value[0]))
            g = round(float(value[1]))
            b = round(float(value[2]))
            a = round(float(value[3]))
        except (TypeError, ValueError):
            return fallback
        return (
            max(0, min(255, r)),
            max(0, min(255, g)),
            max(0, min(255, b)),
            max(0, min(255, a)),
        )
    return fallback


def load_text_tool_settings(settings_service: Any) -> TextToolSettings:
    getter = getattr(settings_service, "get", None)
    if not callable(getter):
        return TextToolSettings()

    default = TextToolSettings()
    word_wrap_raw = getter("text_tool/word_wrap", default.word_wrap)
    family = getter("text_tool/font_family", default.font_family)
    style = getter("text_tool/font_style", default.font_style)
    size_raw = getter("text_tool/font_size_pt", default.font_size_pt)
    color_raw = getter("text_tool/color_rgba", list(default.color_rgba))
    vertical_raw = getter("text_tool/vertical", default.vertical)
    vertical_flow_raw = getter("text_tool/vertical_flow", default.vertical_flow)
    align_raw = getter("text_tool/align", default.align)
    line_height_raw = getter("text_tool/line_height", default.line_height)
    tracking_raw = getter("text_tool/tracking", default.tracking)
    pad_top_raw = getter("text_tool/padding_top", default.padding_top)
    pad_right_raw = getter("text_tool/padding_right", default.padding_right)
    pad_bottom_raw = getter("text_tool/padding_bottom", default.padding_bottom)
    pad_left_raw = getter("text_tool/padding_left", default.padding_left)
    rotate_hw_raw = getter("text_tool/rotate_half_width", default.rotate_half_width)

    try:
        size_pt = float(size_raw)
    except (TypeError, ValueError):
        size_pt = default.font_size_pt
    try:
        line_height = float(line_height_raw)
    except (TypeError, ValueError):
        line_height = default.line_height
    try:
        tracking = float(tracking_raw)
    except (TypeError, ValueError):
        tracking = default.tracking

    def _clamp_pad(raw: Any, fallback: float) -> float:
        try:
            return max(0.0, min(256.0, float(raw)))
        except (TypeError, ValueError):
            return fallback

    align = str(align_raw or default.align).strip().lower()
    if align not in {"left", "center", "right", "justify"}:
        align = default.align

    return TextToolSettings(
        word_wrap=bool(word_wrap_raw),
        font_family=str(family or ""),
        font_style=str(style or ""),
        font_size_pt=max(1.0, min(512.0, size_pt)),
        color_rgba=normalize_color_rgba(color_raw, default.color_rgba),
        vertical=bool(vertical_raw),
        vertical_flow=normalize_vertical_flow(vertical_flow_raw, default=default.vertical_flow),
        align=align,
        line_height=max(0.5, min(4.0, line_height)),
        tracking=max(-64.0, min(128.0, tracking)),
        padding_top=_clamp_pad(pad_top_raw, default.padding_top),
        padding_right=_clamp_pad(pad_right_raw, default.padding_right),
        padding_bottom=_clamp_pad(pad_bottom_raw, default.padding_bottom),
        padding_left=_clamp_pad(pad_left_raw, default.padding_left),
        rotate_half_width=bool(rotate_hw_raw),
    )


def save_text_tool_setting(settings_service: Any, key: str, value: Any) -> None:
    setter = getattr(settings_service, "set", None)
    if not callable(setter):
        return
    setter(f"text_tool/{key}", value)


def load_custom_fonts_from_settings(settings_service: Any) -> tuple[int, int]:
    getter = getattr(settings_service, "get", None)
    if not callable(getter):
        return (0, 0)
    raw_dirs = getter("text_tool/custom_font_dirs", [])
    dirs = _normalize_font_dirs(raw_dirs)
    loaded, failed = load_fonts_from_dirs(dirs)
    return loaded, failed


def load_fonts_from_dirs(font_dirs: Sequence[str]) -> tuple[int, int]:
    loaded = 0
    failed = 0
    for font_dir in _normalize_font_dirs(font_dirs):
        directory = Path(font_dir)
        if not directory.exists() or not directory.is_dir():
            continue
        try:
            iterator = directory.rglob("*")
        except Exception:
            continue
        for path in iterator:
            if not path.is_file() or path.suffix.lower() not in _FONT_EXTENSIONS:
                continue
            normalized = str(path.resolve())
            if normalized in _LOADED_FONT_FILES:
                continue
            try:
                font_id = QFontDatabase.addApplicationFont(normalized)
            except Exception:
                font_id = -1
            if font_id >= 0:
                _LOADED_FONT_FILES.add(normalized)
                loaded += 1
            else:
                failed += 1
    if loaded or failed:
        logger.info(
            "Custom font load completed: loaded=%s failed=%s",
            loaded,
            failed,
        )
    return loaded, failed


def normalize_tracking_runs(
    runs: Any,
    text_length: int,
) -> list[dict[str, Any]]:
    """Normalize and merge tracking_runs, removing invalid or out-of-range entries."""
    if not isinstance(runs, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for r in runs:
        if not isinstance(r, Mapping):
            continue
        try:
            s = int(r["start"])
            e = int(r["end"])
            t = float(r["tracking"])
        except (KeyError, TypeError, ValueError):
            continue
        s = max(0, min(s, text_length))
        e = max(s, min(e, text_length))
        if s >= e:
            continue
        t = max(-64.0, min(128.0, t))
        result.append({"start": s, "end": e, "tracking": t})
    # Sort by start, merge overlapping
    result.sort(key=lambda x: x["start"])
    merged: list[dict[str, Any]] = []
    for r in result:
        if (
            merged
            and r["start"] <= merged[-1]["end"]
            and abs(r["tracking"] - merged[-1]["tracking"]) < 1e-6
        ):
            merged[-1]["end"] = max(merged[-1]["end"], r["end"])
        else:
            merged.append(dict(r))
    return merged


def shift_value_runs(
    runs: list[dict[str, Any]],
    position: int,
    delta: int,
    text_length: int,
) -> list[dict[str, Any]]:
    """Shift start/end in runs after text insertion (delta>0) or deletion (delta<0).

    Works with any run type — all keys besides start/end are preserved.
    """
    if not runs or delta == 0:
        return list(runs)
    result: list[dict[str, Any]] = []
    for r in runs:
        entry = dict(r)
        s = int(entry["start"])
        e = int(entry["end"])
        if delta > 0:
            if s >= position:
                s += delta
            if e >= position:
                e += delta
        else:
            del_start = position
            del_end = position - delta
            if s >= del_end:
                s += delta
                e += delta
            elif s >= del_start:
                s = del_start
                e = max(del_start, e + delta)
            elif e > del_start:
                e = max(s, e + delta)
        s = max(0, min(s, text_length))
        e = max(s, min(e, text_length))
        if s < e:
            entry["start"] = s
            entry["end"] = e
            result.append(entry)
    return result


def shift_tracking_runs(
    runs: list[dict[str, Any]],
    position: int,
    delta: int,
    text_length: int,
) -> list[dict[str, Any]]:
    """Shift tracking_runs after text insertion/deletion."""
    return shift_value_runs(runs, position, delta, text_length)


def merge_tracking_run(
    existing: list[dict[str, Any]],
    start: int,
    end: int,
    tracking: float,
    text_length: int,
) -> list[dict[str, Any]]:
    """Add or replace a tracking run for [start, end) range."""
    return _merge_value_run(
        existing,
        start,
        end,
        "tracking",
        tracking,
        text_length,
        normalize_tracking_runs,
    )


def normalize_line_height_runs(
    runs: Any,
    text_length: int,
) -> list[dict[str, Any]]:
    """Normalize and merge line_height_runs."""
    if not isinstance(runs, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for r in runs:
        if not isinstance(r, Mapping):
            continue
        try:
            s = int(r["start"])
            e = int(r["end"])
            v = float(r["line_height"])
        except (KeyError, TypeError, ValueError):
            continue
        s = max(0, min(s, text_length))
        e = max(s, min(e, text_length))
        if s >= e:
            continue
        v = max(0.5, min(4.0, v))
        result.append({"start": s, "end": e, "line_height": v})
    result.sort(key=lambda x: x["start"])
    merged: list[dict[str, Any]] = []
    for r in result:
        if (
            merged
            and r["start"] <= merged[-1]["end"]
            and abs(r["line_height"] - merged[-1]["line_height"]) < 1e-6
        ):
            merged[-1]["end"] = max(merged[-1]["end"], r["end"])
        else:
            merged.append(dict(r))
    return merged


def merge_line_height_run(
    existing: list[dict[str, Any]],
    start: int,
    end: int,
    line_height: float,
    text_length: int,
) -> list[dict[str, Any]]:
    """Add or replace a line_height run for [start, end) range."""
    return _merge_value_run(
        existing,
        start,
        end,
        "line_height",
        line_height,
        text_length,
        normalize_line_height_runs,
    )


def _merge_value_run(
    existing: list[dict[str, Any]],
    start: int,
    end: int,
    value_key: str,
    value: float,
    text_length: int,
    normalizer: Any,
) -> list[dict[str, Any]]:
    """Generic merge for any value-run type."""
    if start >= end:
        return list(existing)
    result: list[dict[str, Any]] = []
    for r in existing:
        rs = int(r["start"])
        re_ = int(r["end"])
        if re_ <= start or rs >= end:
            result.append(dict(r))
        else:
            if rs < start:
                trimmed = dict(r)
                trimmed["end"] = start
                result.append(trimmed)
            if re_ > end:
                trimmed = dict(r)
                trimmed["start"] = end
                result.append(trimmed)
    result.append({"start": start, "end": end, value_key: value})
    return normalizer(result, text_length)


def ensure_text_vector_object(  # noqa: PLR0912, PLR0915 - preserve optional layout metadata
    *,
    object_id: str | None,
    content: str,
    rect: tuple[float, float, float, float],
    settings: TextToolSettings,
    word_wrap: bool = True,
    wrap_mode_override: str | None = None,
    rotation_deg: float = 0.0,
    previous_meta: Mapping[str, Any] | None = None,
    tracking_runs: list[dict[str, Any]] | None = None,
    line_height_runs: list[dict[str, Any]] | None = None,
) -> VectorObject:
    x, y, w, h = rect
    x = float(x)
    y = float(y)
    w = max(1.0, float(w))
    h = max(1.0, float(h))

    angle = float(rotation_deg)
    center_x = x + (w * 0.5)
    center_y = y + (h * 0.5)
    rad = math.radians(angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)

    def _rotate(px: float, py: float) -> tuple[float, float]:
        if abs(angle) <= 1e-6:
            return (px, py)
        dx = px - center_x
        dy = py - center_y
        return (
            center_x + (dx * cos_a) - (dy * sin_a),
            center_y + (dx * sin_a) + (dy * cos_a),
        )

    anchors = [
        Anchor(pos=_rotate(x, y), kind="corner"),
        Anchor(pos=_rotate(x + w, y), kind="corner"),
        Anchor(pos=_rotate(x + w, y + h), kind="corner"),
        Anchor(pos=_rotate(x, y + h), kind="corner"),
    ]
    path = VectorPath(anchors=anchors, closed=True)

    style = VectorStyle(
        stroke_color=(
            int(settings.color_rgba[0]),
            int(settings.color_rgba[1]),
            int(settings.color_rgba[2]),
        ),
        stroke_width=1.0,
        fill_enabled=False,
        fill_color=None,
    )

    prior = dict(previous_meta) if isinstance(previous_meta, Mapping) else {}
    marker = prior.get(TEXT_META_KEY)
    marker_payload = dict(marker) if isinstance(marker, Mapping) else {}
    previous_document = marker_payload.get("document")
    previous_content = extract_text_content(marker_payload)
    content_text = str(content)
    text_end = max(0, len(content_text))
    # Resolve tracking_runs: use explicit parameter, fall back to previous payload
    resolved_tracking_runs: list[dict[str, Any]] = []
    if tracking_runs is not None:
        resolved_tracking_runs = normalize_tracking_runs(tracking_runs, text_end)
    else:
        prev_runs = marker_payload.get("tracking_runs")
        if isinstance(prev_runs, (list, tuple)):
            resolved_tracking_runs = normalize_tracking_runs(prev_runs, text_end)
    # Resolve line_height_runs: same pattern
    resolved_line_height_runs: list[dict[str, Any]] = []
    if line_height_runs is not None:
        resolved_line_height_runs = normalize_line_height_runs(line_height_runs, text_end)
    else:
        prev_lh = marker_payload.get("line_height_runs")
        if isinstance(prev_lh, (list, tuple)):
            resolved_line_height_runs = normalize_line_height_runs(prev_lh, text_end)
    previous_wrap_mode = None
    previous_layout = marker_payload.get("layout")
    previous_styles = marker_payload.get("style_map")
    if isinstance(previous_layout, Mapping):
        previous_wrap_mode = str(previous_layout.get("wrap_mode", "")).strip().lower()
    has_shape_frame = isinstance(marker_payload.get("shape_frame"), Mapping)
    normalized_wrap_override = str(wrap_mode_override or "").strip().lower()
    if normalized_wrap_override in {"none", "word", "char", "shape"}:
        wrap_mode = normalized_wrap_override
    elif has_shape_frame or previous_wrap_mode == "shape":
        wrap_mode = "shape"
    elif word_wrap:
        wrap_mode = "word"
    else:
        wrap_mode = "none"
    marker_payload.update(
        {
            "schema_version": TEXT_SCHEMA_VERSION,
            "object_type": "text",
            "word_wrap": bool(word_wrap),
            "content": content_text,
            "document": {
                "text": content_text,
                "runs": [
                    {
                        "start": 0,
                        "end": text_end,
                        "style_ref": "default",
                    }
                ],
                "paragraphs": [
                    {
                        "start": 0,
                        "end": text_end,
                        "align": settings.align,
                        "line_height": float(settings.line_height) if word_wrap else 1.0,
                        "vertical_flow": normalize_vertical_flow(
                            settings.vertical_flow,
                            default=VERTICAL_FLOW_LEFT,
                        ),
                    }
                ],
            },
            "style_map": {
                "default": {
                    "font_family": settings.font_family,
                    "font_style": settings.font_style,
                    "font_size_pt": float(settings.font_size_pt),
                    "tracking": float(settings.tracking) if word_wrap else 0.0,
                    "color_rgba": [int(v) for v in settings.color_rgba],
                    "vertical": bool(settings.vertical),
                    "rotate_half_width": bool(settings.rotate_half_width),
                }
            },
            "text_mode": "area" if word_wrap else "point",
            "frame": {
                "x": float(x),
                "y": float(y),
                "width": float(w),
                "height": float(h),
                "auto_size": not word_wrap,
            },
            "transform": {
                "rotation_deg": float(angle),
            },
            "layout": {
                "wrap_mode": wrap_mode,
                "overflow_mode": "clip" if word_wrap else "expand",
                "padding_top": float(settings.padding_top) if word_wrap else 0.0,
                "padding_right": float(settings.padding_right) if word_wrap else 0.0,
                "padding_bottom": float(settings.padding_bottom) if word_wrap else 0.0,
                "padding_left": float(settings.padding_left) if word_wrap else 0.0,
            },
            "font_family": settings.font_family,
            "font_style": settings.font_style,
            "font_size_pt": float(settings.font_size_pt),
            "color_rgba": [int(v) for v in settings.color_rgba],
            "vertical": bool(settings.vertical),
            "vertical_flow": normalize_vertical_flow(
                settings.vertical_flow,
                default=VERTICAL_FLOW_LEFT,
            ),
            "align": settings.align,
            "line_height": float(settings.line_height) if word_wrap else 1.0,
            "tracking": float(settings.tracking) if word_wrap else 0.0,
            "padding_top": float(settings.padding_top) if word_wrap else 0.0,
            "padding_right": float(settings.padding_right) if word_wrap else 0.0,
            "padding_bottom": float(settings.padding_bottom) if word_wrap else 0.0,
            "padding_left": float(settings.padding_left) if word_wrap else 0.0,
            "tracking_runs": resolved_tracking_runs,
            "line_height_runs": resolved_line_height_runs,
            "x": float(x),
            "y": float(y),
            "width": float(w),
            "height": float(h),
            "auto_size": not word_wrap,
            "rotation_deg": float(angle),
            "updated_at": float(time.time()),
        }
    )
    japanese = settings.typography or (
        previous_layout.get("japanese") if isinstance(previous_layout, Mapping) else None
    )
    if isinstance(japanese, Mapping):
        marker_payload["layout"]["japanese"] = normalize_typography(japanese)
    if isinstance(previous_layout, Mapping) and "text_flow" in previous_layout:
        marker_payload["layout"]["text_flow"] = deepcopy(previous_layout["text_flow"])
    marker_payload["document"] = _merge_text_document(
        previous_document, marker_payload["document"], previous_content
    )
    marker_payload["style_map"] = {
        **(deepcopy(previous_styles) if isinstance(previous_styles, Mapping) else {}),
        **marker_payload["style_map"],
    }
    prior[TEXT_META_KEY] = marker_payload

    return VectorObject(
        id=object_id or f"text-{time.time_ns()}",
        paths=[path],
        style=style,
        meta=prior,
        preview=False,
    )


def _merge_text_document(
    previous: Any, current: dict[str, Any], previous_content: str
) -> dict[str, Any]:
    """Keep document extensions when a command builder regenerates a Text object."""
    result = deepcopy(dict(previous)) if isinstance(previous, Mapping) else {}
    result.update(current)
    # Explicit character overrides belong to the existing document runs.
    # Command builders must retain them instead of replacing them with one
    # default run; the shared range mapper also follows regenerated text.
    prior_runs = previous.get("runs") if isinstance(previous, Mapping) else None
    if isinstance(prior_runs, (list, tuple)) and any(
        isinstance(run, Mapping)
        and any(key in run for key in ("color_rgba", "font_family", "font_style", "font_size_pt"))
        for run in prior_runs
    ):
        from loomstroke.core.text_edit_ranges import shift_text_ranges  # noqa: PLC0415

        start, old_end, new_end = changed_text_span(previous_content, current["text"], None)
        result["runs"] = shift_text_ranges(
            deepcopy(prior_runs),
            start=start,
            old_end=old_end,
            new_end=new_end,
            text_length=len(current["text"]),
        )
    content = current["text"]
    prior_paragraphs = previous.get("paragraphs") if isinstance(previous, Mapping) else None
    if isinstance(prior_paragraphs, (list, tuple)) and any(
        isinstance(record, Mapping) and "style_overrides" in record for record in prior_paragraphs
    ):
        from loomstroke.core.text_edit_ranges import shift_text_ranges  # noqa: PLC0415

        start, old_end, new_end = changed_text_span(previous_content, content, None)
        result["paragraphs"] = shift_text_ranges(
            deepcopy(prior_paragraphs),
            start=start,
            old_end=old_end,
            new_end=new_end,
            text_length=len(content),
        )
    if content != previous_content:
        start, old_end, new_end = changed_text_span(previous_content, content, None)
        for key in ANNOTATION_KEYS:
            if key in result:
                result[key] = shift_annotation_ranges(
                    result[key],
                    kind=key,
                    start=start,
                    old_end=old_end,
                    new_end=new_end,
                    text=content,
                )
    return result


def is_text_vector_object(obj: Any) -> bool:
    return extract_text_payload(obj) is not None


def extract_text_payload(obj: Any) -> dict[str, Any] | None:
    meta = getattr(obj, "meta", None)
    if not isinstance(meta, Mapping):
        return None
    payload = meta.get(TEXT_META_KEY)
    if isinstance(payload, Mapping):
        return dict(payload)
    return None


def normalize_text_mode(value: Any, *, default: str = TEXT_MODE_POINT) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in TEXT_MODE_VALUES:
        return candidate
    if default in TEXT_MODE_VALUES:
        return default
    return TEXT_MODE_POINT


def normalize_vertical_flow(value: Any, *, default: str = VERTICAL_FLOW_LEFT) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in VERTICAL_FLOW_VALUES:
        return candidate
    if default in VERTICAL_FLOW_VALUES:
        return default
    return VERTICAL_FLOW_LEFT


def extract_text_content(payload: Mapping[str, Any]) -> str:
    document = payload.get("document")
    if isinstance(document, Mapping):
        text = document.get("text")
        if isinstance(text, str):
            return text
        if text is not None:
            return str(text)
    return str(payload.get("content", "") or "")


def extract_word_wrap(payload: Mapping[str, Any]) -> bool:
    """Extract the word_wrap setting from a text payload."""
    ww = payload.get("word_wrap")
    if ww is not None:
        return bool(ww)
    # Legacy fallback
    mode_raw = payload.get("text_mode")
    if mode_raw is not None:
        return normalize_text_mode(mode_raw) == TEXT_MODE_AREA
    auto_size = payload.get("auto_size")
    if auto_size is not None:
        return not bool(auto_size)
    return True


def vector_bbox_from_paths(obj: Any) -> tuple[float, float, float, float] | None:
    paths = getattr(obj, "paths", None) or []
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    for path in paths:
        anchors = getattr(path, "anchors", None) or []
        for anchor in anchors:
            pos = getattr(anchor, "pos", None)
            if not (isinstance(pos, tuple) and len(pos) == 2):
                continue
            x = float(pos[0])
            y = float(pos[1])
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if min_x == float("inf"):
        return None
    return (min_x, min_y, max_x, max_y)


def _normalize_font_dirs(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    dirs: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            continue
        candidate = raw.strip()
        if not candidate:
            continue
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs
