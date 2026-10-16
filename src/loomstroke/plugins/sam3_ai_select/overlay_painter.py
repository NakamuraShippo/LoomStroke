"""Runtime-only canvas overlay painter for SAM3 AI Select candidates."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from ...ai_selection import (
    MaskAsset,
    Sam3AssetStore,
    Sam3CandidateOverlayItem,
    Sam3SelectionOverlayModel,
    load_selection_mask_qimage,
)

try:  # pragma: no cover - exercised through Qt-backed tests when available
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (
        QColor,
        QFont,
        QGuiApplication,
        QImage,
        QPainter,
        QPainterPath,
        QPen,
    )
except Exception:  # pragma: no cover - defensive import guard for non-Qt probes
    QColor = None  # type: ignore[assignment]
    QFont = None  # type: ignore[assignment]
    QGuiApplication = None  # type: ignore[assignment]
    QImage = None  # type: ignore[assignment]
    QPointF = None  # type: ignore[assignment]
    QPainter = None  # type: ignore[assignment]
    QPainterPath = None  # type: ignore[assignment]
    QPen = None  # type: ignore[assignment]
    QRectF = None  # type: ignore[assignment]
    Qt = None  # type: ignore[assignment]


_AVAILABLE_OUTLINE_WIDTH_PX = 1.6
_SELECTED_OUTLINE_WIDTH_PX = 3.0
# Selection clarity (2026-07-13): the selected candidate gets a white halo
# under its colored outline and every candidate gets a rank badge so the
# refine/apply target is unmistakable on any artwork.
_SELECTED_HALO_WIDTH_PX = 6.0
_MULTI_SELECTED_HALO_WIDTH_PX = 4.0
_SELECTED_HALO_RGBA = (255, 255, 255, 235)
_BADGE_HEIGHT_PX = 15.0
_BADGE_FONT_PX = 10.0
_BADGE_SCORE_HEIGHT_PX = 11.0
_BADGE_SCORE_FONT_PX = 8.0
_BADGE_MARGIN_PX = 2.0
_BADGE_TEXT_RGBA = (255, 255, 255, 255)
_BADGE_AVAILABLE_BG_RGBA = (32, 32, 32, 175)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _MaskOverlayEntry:
    image: Any
    mask: Any
    origin_x: int
    origin_y: int
    # Traced lazily on first paint; the cache lives as long as the painter
    # (one per overlay-model sync), so the candidate mask cannot change
    # underneath it (assets are sha256-verified at load).
    outline_path: Any = None
    outline_traced: bool = False


class Sam3AiSelectOverlayPainter:
    """Paint SAM3 candidate previews using an existing overlay model."""

    def __init__(
        self,
        overlay_model: Sam3SelectionOverlayModel,
        *,
        asset_store: Sam3AssetStore | None = None,
        multi_selected_ids: tuple[str, ...] = (),
    ) -> None:
        self._overlay_model = overlay_model
        self._asset_store = asset_store
        self._multi_selected_ids = frozenset(multi_selected_ids)
        self._mask_cache = _build_mask_cache(overlay_model, asset_store)

    @property
    def overlay_model(self) -> Sam3SelectionOverlayModel:
        return self._overlay_model

    @property
    def asset_store(self) -> Sam3AssetStore | None:
        return self._asset_store

    def __call__(self, painter: Any) -> None:
        if QPainter is None or QColor is None or QPen is None or QRectF is None:
            return
        if not isinstance(painter, QPainter):
            return

        items = [item for item in self._ordered_visible_items() if item.clipped_bbox is not None]
        if not items:
            return

        painter.save()
        try:
            inv_scale = 1.0 / max(_painter_scale(painter), 0.01)
            for item in items:
                _paint_item(
                    painter,
                    item,
                    inv_scale=inv_scale,
                    mask_entry=self._mask_cache.get(item.candidate_id),
                    multi_selected=item.candidate_id in self._multi_selected_ids,
                )
            if len(items) >= 2:
                # Rank badges tie each canvas shape to its candidate-list
                # row; drawn last so they stay on top of overlapping masks.
                for item in items:
                    _paint_rank_badge(painter, item, inv_scale=inv_scale)
        finally:
            painter.restore()

    def _ordered_visible_items(self) -> tuple[Sam3CandidateOverlayItem, ...]:
        visible = self._overlay_model.visible_items
        return tuple(
            sorted(
                visible,
                key=lambda item: (item.state == "selected", item.rank),
            )
        )


def _build_mask_cache(
    overlay_model: Sam3SelectionOverlayModel,
    asset_store: Sam3AssetStore | None,
) -> dict[str, _MaskOverlayEntry]:
    if asset_store is None or QImage is None or QColor is None or QPainter is None:
        return {}

    cache: dict[str, _MaskOverlayEntry] = {}
    for item in overlay_model.visible_items:
        if item.clipped_bbox is None or not item.mask_asset_id:
            continue
        entry = _load_mask_entry(overlay_model, asset_store, item)
        if entry is not None:
            cache[item.candidate_id] = entry
    return cache


def _load_mask_entry(
    overlay_model: Sam3SelectionOverlayModel,
    asset_store: Sam3AssetStore,
    item: Sam3CandidateOverlayItem,
) -> _MaskOverlayEntry | None:
    try:
        asset = asset_store.load_mask_asset(item.mask_asset_id)
        if asset.source_candidate_id and asset.source_candidate_id != item.candidate_id:
            return None
        mask = load_selection_mask_qimage(asset_store, asset)
        origin = _mask_origin(overlay_model, asset, mask)
        if origin is None:
            return None
        tinted = _tinted_mask_image(mask, item.style.fill_rgba)
        if tinted is None:
            return None
        return _MaskOverlayEntry(tinted, mask, origin[0], origin[1])
    except Exception:
        logger.debug("SAM3 AI Select mask overlay asset load failed", exc_info=True)
        return None


def _mask_origin(
    overlay_model: Sam3SelectionOverlayModel,
    asset: MaskAsset,
    mask: Any,
) -> tuple[int, int] | None:
    source_width = int(overlay_model.source_ref.width)
    source_height = int(overlay_model.source_ref.height)
    if (
        source_width > 0
        and source_height > 0
        and mask.width() == source_width
        and mask.height() == source_height
    ):
        return (0, 0)

    x, y, width, height = (int(value) for value in asset.bbox)
    if width <= 0 or height <= 0:
        return None
    if mask.width() != width or mask.height() != height:
        return None
    return (x, y)


def _tinted_mask_image(mask: Any, rgba: tuple[int, int, int, int]) -> Any | None:
    if QImage is None or QColor is None or QPainter is None:
        return None
    if mask.isNull() or mask.width() <= 0 or mask.height() <= 0:
        return None

    image = QImage(mask.width(), mask.height(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(_qcolor(rgba))
    painter = QPainter(image)
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask)
    finally:
        painter.end()
    return image


def _paint_item(
    painter: Any,
    item: Sam3CandidateOverlayItem,
    *,
    inv_scale: float,
    mask_entry: _MaskOverlayEntry | None = None,
    multi_selected: bool = False,
) -> None:
    clipped_bbox = item.clipped_bbox
    if clipped_bbox is None:
        return
    _, _, width, height = clipped_bbox
    if width <= 0 or height <= 0:
        return

    if mask_entry is not None:
        _paint_mask_entry(painter, item, mask_entry)
        _paint_mask_outline(
            painter, item, mask_entry, inv_scale=inv_scale, multi_selected=multi_selected
        )
        return

    _paint_bbox(painter, item, inv_scale=inv_scale, multi_selected=multi_selected)


def _paint_mask_entry(
    painter: Any,
    item: Sam3CandidateOverlayItem,
    entry: _MaskOverlayEntry,
) -> None:
    if QRectF is None or QPointF is None or Qt is None:
        return
    clipped_bbox = item.clipped_bbox
    if clipped_bbox is None:
        return

    painter.save()
    try:
        painter.setClipRect(_rect_from_bbox(clipped_bbox), Qt.ClipOperation.IntersectClip)
        painter.drawImage(QPointF(float(entry.origin_x), float(entry.origin_y)), entry.image)
    finally:
        painter.restore()


def _paint_bbox(
    painter: Any,
    item: Sam3CandidateOverlayItem,
    *,
    inv_scale: float,
    multi_selected: bool = False,
) -> None:
    fill = _qcolor(item.style.fill_rgba)
    outline = _qcolor(item.style.outline_rgba)
    width_px = (
        _SELECTED_OUTLINE_WIDTH_PX if item.state == "selected" else _AVAILABLE_OUTLINE_WIDTH_PX
    )
    rect = _rect_from_bbox(item.clipped_bbox)
    if item.state == "selected" and Qt is not None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_qcolor(_SELECTED_HALO_RGBA), _SELECTED_HALO_WIDTH_PX * inv_scale))
        painter.drawRect(rect)
    elif multi_selected and Qt is not None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_multi_selected_pen(inv_scale))
        painter.drawRect(rect)
    painter.setBrush(fill)
    painter.setPen(QPen(outline, width_px * inv_scale))
    painter.drawRect(rect)


def _multi_selected_pen(inv_scale: float) -> Any:
    """Dashed white halo marking Shift+click multi-selection members."""

    pen = QPen(_qcolor(_SELECTED_HALO_RGBA), _MULTI_SELECTED_HALO_WIDTH_PX * inv_scale)
    pen.setStyle(Qt.PenStyle.DashLine)
    return pen


def _paint_mask_outline(
    painter: Any,
    item: Sam3CandidateOverlayItem,
    entry: _MaskOverlayEntry,
    *,
    inv_scale: float,
    multi_selected: bool = False,
) -> None:
    """Draw the candidate's actual mask contour (never the bounding rect).

    A failed trace draws nothing extra — the tinted mask fill stays the
    only cue; the rectangle fallback is reserved for maskless candidates.
    """

    if Qt is None:
        return
    path = _entry_outline_path(entry)
    if path is None or path.isEmpty():
        return
    outline = _qcolor(item.style.outline_rgba)
    width_px = (
        _SELECTED_OUTLINE_WIDTH_PX if item.state == "selected" else _AVAILABLE_OUTLINE_WIDTH_PX
    )
    painter.save()
    try:
        painter.setClipRect(_rect_from_bbox(item.clipped_bbox), Qt.ClipOperation.IntersectClip)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if item.state == "selected":
            painter.setPen(QPen(_qcolor(_SELECTED_HALO_RGBA), _SELECTED_HALO_WIDTH_PX * inv_scale))
            painter.drawPath(path)
        elif multi_selected:
            painter.setPen(_multi_selected_pen(inv_scale))
            painter.drawPath(path)
        painter.setPen(QPen(outline, width_px * inv_scale))
        painter.drawPath(path)
    finally:
        painter.restore()


def _paint_rank_badge(painter: Any, item: Sam3CandidateOverlayItem, *, inv_scale: float) -> None:
    """Draw the candidate's rank number (and score below it) at its bbox corner."""

    if QRectF is None or QFont is None or QPen is None or Qt is None:
        return
    clipped_bbox = item.clipped_bbox
    if clipped_bbox is None:
        return

    rank_text = str(item.rank)
    score_text = f"{round(item.score * 100)}%" if item.score is not None else None
    selected = item.state == "selected"
    rank_height = _BADGE_HEIGHT_PX * inv_scale
    score_height = _BADGE_SCORE_HEIGHT_PX * inv_scale if score_text else 0.0
    height = rank_height + score_height
    text_length = max(len(rank_text), len(score_text or ""))
    width = max(rank_height, (7.0 + 6.0 * text_length) * inv_scale)
    margin = _BADGE_MARGIN_PX * inv_scale
    rect = QRectF(clipped_bbox[0] + margin, clipped_bbox[1] + margin, width, height)

    if selected:
        red, green, blue, _alpha = item.style.outline_rgba
        background = _qcolor((red, green, blue, 235))
    else:
        background = _qcolor(_BADGE_AVAILABLE_BG_RGBA)

    painter.save()
    try:
        if selected:
            painter.setPen(QPen(_qcolor(_SELECTED_HALO_RGBA), max(0.5, 1.2 * inv_scale)))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        radius = 3.0 * inv_scale
        painter.drawRoundedRect(rect, radius, radius)
        # Text rendering needs a QGuiApplication (font database); headless
        # consumers still get the badge chip without the number.
        if QGuiApplication is not None and QGuiApplication.instance() is not None:
            font = QFont(painter.font())
            font.setPixelSize(max(1, round(_BADGE_FONT_PX * inv_scale)))
            font.setBold(selected)
            painter.setFont(font)
            painter.setPen(QPen(_qcolor(_BADGE_TEXT_RGBA), 1.0))
            rank_rect = QRectF(rect.x(), rect.y(), width, rank_height)
            painter.drawText(rank_rect, Qt.AlignmentFlag.AlignCenter, rank_text)
            if score_text:
                score_font = QFont(font)
                score_font.setPixelSize(max(1, round(_BADGE_SCORE_FONT_PX * inv_scale)))
                score_font.setBold(False)
                painter.setFont(score_font)
                score_rect = QRectF(
                    rect.x(),
                    rect.y() + rank_height - (2.0 * inv_scale),
                    width,
                    score_height,
                )
                painter.drawText(score_rect, Qt.AlignmentFlag.AlignCenter, score_text)
    finally:
        painter.restore()


def _entry_outline_path(entry: _MaskOverlayEntry) -> Any | None:
    if not entry.outline_traced:
        entry.outline_traced = True
        try:
            path = _mask_outline_path(entry.mask)
        except Exception:
            logger.debug("SAM3 AI Select mask outline trace failed", exc_info=True)
            path = None
        if path is not None and (entry.origin_x or entry.origin_y):
            path.translate(float(entry.origin_x), float(entry.origin_y))
        entry.outline_path = path
        # The raw Alpha8 copy is only needed for the one-shot trace.
        entry.mask = None
    return entry.outline_path


def _mask_outline_path(mask: Any) -> Any | None:
    """Trace the pixel boundary of an Alpha8 mask as a QPainterPath.

    Same visual style as the canvas marching ants: horizontal and vertical
    segments along every selected/unselected pixel edge. Works for both
    bbox-cropped and full-canvas masks (the caller translates by origin).
    """

    if QPainterPath is None or QImage is None:
        return None
    import numpy as np  # noqa: PLC0415

    width, height = mask.width(), mask.height()
    if width <= 0 or height <= 0:
        return None
    source = mask
    if source.format() != QImage.Format.Format_Alpha8:
        source = source.convertToFormat(QImage.Format.Format_Alpha8)
    bytes_per_line = source.bytesPerLine()
    pointer = source.bits()
    if pointer is None:
        return None
    if hasattr(pointer, "setsize"):
        pointer.setsize(bytes_per_line * height)
    try:
        raw = bytes(pointer)
    except Exception:
        return None
    selected = np.frombuffer(raw, dtype=np.uint8).reshape(height, bytes_per_line)[:, :width] > 127
    if not np.any(selected):
        return None

    padded = np.zeros((height + 2, width + 2), dtype=bool)
    padded[1 : height + 1, 1 : width + 1] = selected

    path = QPainterPath()
    horizontal_diff = padded[:-1, :] != padded[1:, :]
    for y in range(height + 1):
        _append_boundary_runs(path, np, horizontal_diff[y, 1 : width + 1], y, horizontal=True)
    vertical_diff = padded[:, :-1] != padded[:, 1:]
    for x in range(width + 1):
        _append_boundary_runs(path, np, vertical_diff[1 : height + 1, x], x, horizontal=False)
    return path


def _append_boundary_runs(
    path: Any,
    np: Any,
    edges: Any,
    fixed: int,
    *,
    horizontal: bool,
) -> None:
    if not np.any(edges):
        return
    changes = np.diff(np.concatenate(([False], edges, [False])).astype(np.int8))
    for start, end in zip(np.where(changes == 1)[0], np.where(changes == -1)[0], strict=True):
        if horizontal:
            path.moveTo(float(start), float(fixed))
            path.lineTo(float(end), float(fixed))
        else:
            path.moveTo(float(fixed), float(start))
            path.lineTo(float(fixed), float(end))


def _rect_from_bbox(bbox: tuple[int, int, int, int] | None) -> Any:
    if QRectF is None:
        return None
    if bbox is None:
        return QRectF()
    x, y, width, height = bbox
    return QRectF(float(x), float(y), float(width), float(height))


def _qcolor(rgba: tuple[int, int, int, int]) -> Any:
    red, green, blue, alpha = (max(0, min(255, int(value))) for value in rgba)
    return QColor(red, green, blue, alpha)


def _painter_scale(painter: Any) -> float:
    transform = painter.transform()
    return math.sqrt(float(transform.m11()) ** 2 + float(transform.m21()) ** 2)


__all__ = ["Sam3AiSelectOverlayPainter"]
