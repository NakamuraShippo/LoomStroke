"""Shared native text authoring and fit measurement for ComicPagePlan."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from math import ceil, floor
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QFontMetricsF, QGuiApplication, QImage, QPainter

from loomstroke.core.japanese_text.grapheme_clusters import grapheme_spans
from loomstroke.core.japanese_text.typography_layout import build_japanese_layout
from loomstroke.core.japanese_text.typography_painter import draw_japanese_text_object
from loomstroke.core.vector_text_render_primitives import TextRenderPrimitives
from loomstroke.plugins.text_tool.common import TextToolSettings, ensure_text_vector_object
from loomstroke.plugins.vector_tools.common import VectorObject

from .comic_text_capacity import ComicTextCapacity
from .comic_text_layout import (
    DEFAULT_FONT_SIZE,
    MAX_FONT_SIZE,
    MIN_FONT_SIZE,
    ComicTextLayoutResult,
    fit_text_box,
)

PADDING = 8.0
# Conservative fallback inset when native font metrics are unavailable.
# Native authoring measures each glyph's ink overhang instead of using this.
GLYPH_GUARD = 2.0


@dataclass(frozen=True, slots=True)
class ComicNativeText:
    object: VectorObject
    fit: ComicTextLayoutResult


def build_comic_native_text(
    box: Mapping[str, Any],
    *,
    object_id: str = "comic-preview-text",
    qt_available: bool | None = None,
) -> ComicNativeText:
    """Use the editable payload's glyph geometry for preview and fit reports.

    Plan fontSize is in pixels; native text metadata stores points. The legacy
    conservative fitter remains available when there is no QGuiApplication.
    """
    if qt_available is None:
        qt_available = isinstance(QGuiApplication.instance(), QGuiApplication)
    style = _mapping(box.get("textStyle"))
    rect_value = _mapping(box.get("rect"))
    rect = (
        _number(rect_value.get("x"), 0.0),
        _number(rect_value.get("y"), 0.0),
        max(1.0, _number(rect_value.get("width"), 1.0)),
        max(1.0, _number(rect_value.get("height"), 1.0)),
    )
    text = str(box.get("text") or "")[:2000]
    size = style.get("fontSize")
    size = size if isinstance(size, int) and not isinstance(size, bool) else DEFAULT_FONT_SIZE
    size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, size))
    mode = str(style.get("fit") or "shrink_to_fit")
    if mode not in {"none", "wrap", "shrink_to_fit", "truncate"}:
        mode = "none"
    vertical = style.get("vertical") is True

    def at_size(font_size: int, content: str = text) -> ComicNativeText:
        padding = (PADDING,) * 4 if qt_available else (PADDING + GLYPH_GUARD,) * 4
        obj = _text_object(
            box, style, content, rect, font_size, mode, vertical, object_id, qt_available, padding
        )
        if qt_available:
            layout = build_japanese_layout(obj.meta["text_tool"])
            guard = _measured_ink_guard(layout)
            if any(guard):
                obj = _text_object(
                    box,
                    style,
                    content,
                    rect,
                    font_size,
                    mode,
                    vertical,
                    object_id,
                    qt_available,
                    tuple(PADDING + edge for edge in guard),
                )
                layout = build_japanese_layout(obj.meta["text_tool"])
            fit = _native_fit(obj, font_size, layout)
        else:
            inset = PADDING + GLYPH_GUARD
            width, height = max(0.0, rect[2] - 2 * inset), max(0.0, rect[3] - 2 * inset)
            fit = fit_text_box(
                content,
                width=height if vertical else width,
                height=width if vertical else height,
                font_size=font_size,
                fit_mode="wrap" if mode in {"shrink_to_fit", "truncate"} else mode,
            )
            if vertical:
                fit = replace(
                    fit,
                    overflow_horizontal=fit.overflow_vertical,
                    overflow_vertical=fit.overflow_horizontal,
                    warnings=(*fit.warnings, "vertical_text_fit_requires_qgui_application"),
                )
        return ComicNativeText(obj, fit)

    if mode == "shrink_to_fit":
        capacity = (
            ComicTextCapacity(
                text,
                str(style.get("fontFamily") or ""),
                vertical,
                style.get("rotateHalfWidth", vertical) is True,
                max(0.0, rect[2] - 2 * PADDING),
                max(0.0, rect[3] - 2 * PADDING),
            )
            if qt_available
            else None
        )
        # Font hinting and ink guards change line breaks non-monotonically.
        # Check every possible size in descending order; only a proven minimum
        # capacity bound may skip a size. Always measure the minimum size for
        # an honest overflow report when no candidate fits.
        for font_size in range(size, MIN_FONT_SIZE - 1, -1):
            if font_size > MIN_FONT_SIZE and capacity and capacity.cannot_fit(font_size):
                continue
            result = at_size(font_size)
            if result.fit.fits:
                break
        if not result.fit.fits:
            result = replace(
                result,
                fit=replace(
                    result.fit,
                    warnings=(*result.fit.warnings, "text_overflow_after_shrink"),
                ),
            )
    else:
        result = at_size(size)
    if mode == "truncate" and not result.fit.fits:
        result = _truncate_text(at_size, size, text)
    return result


def _truncate_text(at_size: Callable[[int, str], ComicNativeText], size: int, text: str):
    # Truncation is explicit authoring intent. Only complete graphemes are
    # removed; both native and preview receive the same visible text.
    stops = [0, *(end for _start, end in grapheme_spans(text))]
    low, high = 0, len(stops) - 1
    best = at_size(size, "…")
    if not best.fit.fits:
        return replace(
            best,
            fit=replace(best.fit, warnings=(*best.fit.warnings, "text_overflow_after_truncate")),
        )
    while low <= high:
        middle = (low + high) // 2
        candidate = at_size(size, text[: stops[middle]] + "…")
        if candidate.fit.fits:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return replace(
        best,
        fit=replace(best.fit, warnings=(*best.fit.warnings, "text_would_truncate")),
    )


def draw_comic_native_text(painter: QPainter, text: ComicNativeText) -> None:
    """Paint through the same glyph painter used by editable canvas text."""
    payload = text.object.meta["text_tool"]
    context = TextRenderPrimitives.text_object_render_context(
        payload=payload,
        bounds=None,
        selection=None,
        object_id=text.object.id,
        layer_id="comic-preview",
        is_preview_object=False,
    )
    if context is not None:
        layout = build_japanese_layout(payload)
        rect = layout.frame.united(layout.ink_bounds).adjusted(-2, -2, 2, 2)
        device = painter.device()
        rect = rect.intersected(QRectF(0, 0, device.width(), device.height()))
        if rect.isEmpty():
            return
        x, y = floor(rect.left()), floor(rect.top())
        image = QImage(
            ceil(rect.right()) - x,
            ceil(rect.bottom()) - y,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        # Transparent native rasters use grayscale AA. Drawing directly onto
        # an opaque preview can select different subpixel AA and change pixels.
        raster = QPainter(image)
        try:
            raster.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            raster.translate(-x, -y)
            draw_japanese_text_object(raster, context)
        finally:
            raster.end()
        painter.drawImage(x, y, image)


def _text_object(box, style, text, rect, size, mode, vertical, object_id, qt_available, padding):
    family = str(style.get("fontFamily") or "")
    points = float(size) * 0.75
    if qt_available:
        font = QFont(family) if family else QFont()
        font.setPixelSize(size)
        points = QFontInfo(font).pointSizeF()
    color = QColor(str(box.get("textColor") or "#111111"))
    if not color.isValid():
        color = QColor(17, 17, 17, 255)
    rotate = style.get("rotateHalfWidth", vertical) is True
    settings = TextToolSettings(
        word_wrap=True,
        font_family=family,
        font_size_pt=points,
        color_rgba=color.getRgb(),
        vertical=vertical,
        vertical_flow="right" if style.get("verticalFlow") == "right" else "left",
        rotate_half_width=rotate,
        align="center",
        line_height=1.15,
        padding_top=padding[0],
        padding_right=padding[1],
        padding_bottom=padding[2],
        padding_left=padding[3],
        typography={"enabled": True, "latin_orientation": "sideways" if rotate else "upright"},
    )
    obj = ensure_text_vector_object(
        object_id=object_id,
        content=text,
        rect=rect,
        settings=settings,
        word_wrap=True,
        wrap_mode_override="none" if mode == "none" else "word",
    )
    obj.meta["text_tool"]["rotate_half_width"] = rotate
    return obj


def _measured_ink_guard(layout) -> tuple[float, float, float, float]:
    """Reserve each glyph's actual ink overhang beyond its logical cell.

    Cell-relative measurement remains valid when padding changes the line
    breaks. Ceil plus one pixel accounts for the enclosing antialiased raster.
    """
    guard = [0.0] * 4
    ink_cache, metrics_cache = {}, {}
    for glyph in layout.glyphs:
        key = glyph.font.toString()
        metrics = metrics_cache.get(key)
        if metrics is None:
            metrics = metrics_cache[key] = QFontMetricsF(glyph.font)
        ink = ink_cache.get((key, glyph.text))
        if ink is None:
            ink = ink_cache[(key, glyph.text)] = metrics.tightBoundingRect(glyph.text)
        ink = glyph.transform.mapRect(ink)
        if ink.isEmpty():
            continue
        cell = glyph.cell
        for index, overhang in enumerate(
            (
                cell.top() - ink.top(),
                ink.right() - cell.right(),
                ink.bottom() - cell.bottom(),
                cell.left() - ink.left(),
            )
        ):
            guard[index] = max(guard[index], overhang)
    return tuple(float(ceil(edge) + 1) if edge > 0 else 0.0 for edge in guard)


def _native_fit(obj: VectorObject, size: int, layout) -> ComicTextLayoutResult:
    payload = obj.meta["text_tool"]
    bounds = layout.logical_bounds.united(layout.ink_bounds)
    saved = payload["frame"]
    frame = QRectF(
        saved["x"] + PADDING,
        saved["y"] + PADDING,
        max(0.0, saved["width"] - 2 * PADDING),
        max(0.0, saved["height"] - 2 * PADDING),
    )
    has_text = bool(payload["content"])
    horizontal = has_text and (bounds.left() < frame.left() or bounds.right() > frame.right())
    vertical = has_text and (bounds.top() < frame.top() or bounds.bottom() > frame.bottom())
    fits = (
        not horizontal
        and not vertical
        and not any("overflow" in issue and issue != "ink_overflow" for issue in layout.diagnostics)
    )
    return ComicTextLayoutResult(
        fits=fits,
        font_size=size,
        line_count=len(layout.lines) if has_text else 0,
        overflow_horizontal=horizontal,
        overflow_vertical=vertical,
        warnings=() if fits else ("text_overflow",),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
