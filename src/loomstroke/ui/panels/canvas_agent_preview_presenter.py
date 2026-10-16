"""Runtime-only Canvas Agent visual preview presenter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen


class CanvasAgentOverlayPreviewService:
    """Present Canvas Agent overlay metadata without mutating document state."""

    def __init__(self, canvas: Any | None = None) -> None:
        self._canvas: Any | None = canvas
        self._installed_canvas: Any | None = None
        self._active_visual_preview_id: str | None = None
        self._active_overlay: dict[str, Any] | None = None
        self._last_clear_reason: str | None = None

    @property
    def active_visual_preview_id(self) -> str | None:
        return self._active_visual_preview_id

    @property
    def active_overlay(self) -> dict[str, Any] | None:
        return dict(self._active_overlay) if self._active_overlay is not None else None

    @property
    def last_clear_reason(self) -> str | None:
        return self._last_clear_reason

    def set_canvas(self, canvas: Any | None) -> None:
        if canvas is self._canvas:
            return
        self.clear(reason="canvas_changed")
        self._canvas = canvas

    def show_overlay(
        self,
        overlay: Mapping[str, Any],
        *,
        visual_preview_id: str,
    ) -> dict[str, Any]:
        self.clear(reason="replace")
        self._active_visual_preview_id = str(visual_preview_id)
        self._active_overlay = _safe_overlay(overlay)
        setter = getattr(self._canvas, "set_canvas_agent_overlay_fn", None)
        installed = False
        if callable(setter):
            with suppress(Exception):
                setter(_CanvasAgentOverlayPainter(self._active_overlay))
                self._installed_canvas = self._canvas
                installed = True
        return {
            "visualPreviewId": self._active_visual_preview_id,
            "overlayInstalled": installed,
        }

    def clear(self, *, reason: str) -> None:
        canvas = self._installed_canvas
        setter = getattr(canvas, "set_canvas_agent_overlay_fn", None)
        if callable(setter):
            with suppress(Exception):
                setter(None)
        self._installed_canvas = None
        self._active_visual_preview_id = None
        self._active_overlay = None
        self._last_clear_reason = reason


class CanvasAgentPreviewPresenter:
    """Bridge `canvas_visual_preview.v1` metadata to a canvas overlay hook."""

    def __init__(
        self,
        overlay_service: CanvasAgentOverlayPreviewService | None = None,
    ) -> None:
        self._overlay_service = overlay_service or CanvasAgentOverlayPreviewService()

    @property
    def active_visual_preview_id(self) -> str | None:
        return self._overlay_service.active_visual_preview_id

    @property
    def active_overlay(self) -> dict[str, Any] | None:
        return self._overlay_service.active_overlay

    def set_canvas(self, canvas: Any | None) -> None:
        self._overlay_service.set_canvas(canvas)

    def present(
        self,
        visual_preview: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(visual_preview, Mapping):
            self.clear(reason="missing_visual_preview")
            return None
        visual_preview_id = str(visual_preview.get("visualPreviewId") or "").strip()
        overlay = visual_preview.get("canvasOverlay")
        if not visual_preview_id or not isinstance(overlay, Mapping):
            self.clear(reason="missing_overlay")
            return None
        return self._overlay_service.show_overlay(
            overlay,
            visual_preview_id=visual_preview_id,
        )

    def clear(self, *, reason: str) -> None:
        self._overlay_service.clear(reason=reason)


class _CanvasAgentOverlayPainter:
    def __init__(self, overlay: Mapping[str, Any]) -> None:
        self._overlay = _safe_overlay(overlay)

    def __call__(self, painter: QPainter) -> None:
        painter.save()
        try:
            self._draw_targets(painter)
            self._draw_selections(painter)
            self._draw_ghosts(painter)
        finally:
            painter.restore()

    def _draw_targets(self, painter: QPainter) -> None:
        pen = QPen(QColor(255, 178, 0, 220), 2, Qt.PenStyle.DashLine)
        brush = QColor(255, 178, 0, 28)
        painter.setPen(pen)
        for item in _sequence(self._overlay.get("targetHighlights")):
            rect = _rect(item)
            if rect is None:
                continue
            painter.fillRect(rect, brush)
            painter.drawRect(rect)

    def _draw_selections(self, painter: QPainter) -> None:
        pen = QPen(QColor(0, 170, 210, 230), 2, Qt.PenStyle.DashDotLine)
        brush = QColor(0, 170, 210, 24)
        painter.setPen(pen)
        for item in _sequence(self._overlay.get("selectionHighlights")):
            rect = _rect(item)
            if rect is None:
                continue
            painter.fillRect(rect, brush)
            painter.drawRect(rect)

    def _draw_ghosts(self, painter: QPainter) -> None:
        pen = QPen(QColor(120, 90, 255, 180), 1, Qt.PenStyle.DotLine)
        brush = QColor(120, 90, 255, 18)
        painter.setPen(pen)
        for item in _sequence(self._overlay.get("ghostLayers")):
            rect = _rect(item)
            if rect is None:
                continue
            painter.fillRect(rect, brush)
            painter.drawRect(rect)


def _safe_overlay(overlay: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ghostLayers": _safe_sequence(overlay.get("ghostLayers")),
        "targetHighlights": _safe_sequence(overlay.get("targetHighlights")),
        "selectionHighlights": _safe_sequence(overlay.get("selectionHighlights")),
        "nonTargetLayerIds": [
            str(item) for item in _sequence(overlay.get("nonTargetLayerIds")) if str(item).strip()
        ],
    }


def _safe_sequence(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in _sequence(value) if isinstance(item, Mapping)]


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _rect(item: Any) -> QRectF | None:
    if not isinstance(item, Mapping):
        return None
    bounds = item.get("bounds")
    if isinstance(bounds, Mapping):
        try:
            return QRectF(
                float(bounds.get("x", 0)),
                float(bounds.get("y", 0)),
                float(bounds.get("width", 0)),
                float(bounds.get("height", 0)),
            )
        except (TypeError, ValueError):
            return None
    if isinstance(bounds, Sequence) and not isinstance(bounds, (str, bytes)) and len(bounds) >= 4:
        try:
            return QRectF(float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
        except (TypeError, ValueError):
            return None
    return None


__all__ = [
    "CanvasAgentOverlayPreviewService",
    "CanvasAgentPreviewPresenter",
]
