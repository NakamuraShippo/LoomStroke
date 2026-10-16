"""Preview, thumbnail, and render diagnostics sync for MainWindow."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from contextlib import suppress
from math import isfinite
from typing import Any, cast

import numpy as np
from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)

_RENDER_DIAGNOSTICS_TRUTHY = {"1", "true", "yes", "on"}
_PREVIEW_READBACK_RETRY_MS = 16


class MainWindowPreviewDiagnosticsCoordinator:
    """Own runtime-only preview and thumbnail diagnostics counters."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def enabled(self) -> bool:
        return os.environ.get("LOOMSTROKE_RENDER_DIAGNOSTICS", "").strip().lower() in (
            _RENDER_DIAGNOSTICS_TRUTHY
        )

    def record(self, name: str, elapsed_ms: float) -> None:
        if not self.enabled():
            return
        diagnostics = getattr(self._window, "_canvas_performance_diagnostics", None)
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            self._window._canvas_performance_diagnostics = diagnostics
        count_key = f"{name}_count"
        ms_key = f"{name}_ms"
        total_key = f"{name}_total_ms"
        diagnostics[count_key] = int(diagnostics.get(count_key, 0)) + 1
        diagnostics[ms_key] = float(elapsed_ms)
        diagnostics[total_key] = float(diagnostics.get(total_key, 0.0)) + float(elapsed_ms)

    def snapshot(self) -> dict[str, Any]:
        diagnostics = self._default_counters()
        stored = getattr(self._window, "_canvas_performance_diagnostics", None)
        if isinstance(stored, dict):
            diagnostics.update(stored)
        canvas = getattr(self._window, "canvas", None)
        get_render_diagnostics = getattr(canvas, "get_render_diagnostics", None)
        if callable(get_render_diagnostics):
            diagnostics.update(get_render_diagnostics())
        return diagnostics

    @staticmethod
    def _default_counters() -> dict[str, Any]:
        return {
            "preview_update_count": 0,
            "preview_update_ms": 0.0,
            "preview_update_total_ms": 0.0,
            "page_thumbnail_encode_count": 0,
            "page_thumbnail_encode_ms": 0.0,
            "page_thumbnail_encode_total_ms": 0.0,
            "page_thumbnail_update_count": 0,
            "page_thumbnail_update_ms": 0.0,
            "page_thumbnail_update_total_ms": 0.0,
            "preview_deferred_count": 0,
            "preview_deferred_ms": 0.0,
            "preview_deferred_total_ms": 0.0,
            "preview_readback_deferred_count": 0,
            "preview_readback_deferred_ms": 0.0,
            "preview_readback_deferred_total_ms": 0.0,
            "preview_transaction_deferred_count": 0,
            "preview_transaction_deferred_ms": 0.0,
            "preview_transaction_deferred_total_ms": 0.0,
            "layer_thumbnail_update_count": 0,
            "layer_thumbnail_update_ms": 0.0,
            "layer_thumbnail_update_total_ms": 0.0,
            "layer_thumbnail_deferred_count": 0,
            "layer_thumbnail_deferred_ms": 0.0,
            "layer_thumbnail_deferred_total_ms": 0.0,
            "thumbnail_idle_flush_scheduled_count": 0,
            "thumbnail_idle_flush_scheduled_ms": 0.0,
            "thumbnail_idle_flush_scheduled_total_ms": 0.0,
            "thumbnail_idle_flush_count": 0,
            "thumbnail_idle_flush_ms": 0.0,
            "thumbnail_idle_flush_total_ms": 0.0,
            "native_preview_render_count": 0,
            "native_preview_render_ms": 0.0,
            "native_preview_readback_ms": 0.0,
            "native_preview_width": 0,
            "native_preview_height": 0,
            "native_preview_readback_bytes": 0,
            # Full-resolution export readbacks triggered by LIVE preview
            # updates.  Must stay 0; explicit save/export is tracked
            # separately (export_readback_ms).
            "live_export_readback_count": 0,
        }


class MainWindowPreviewSyncCoordinator:
    """Coordinate preview image, thumbnails, and diagnostics updates."""

    def __init__(
        self,
        window: Any,
        *,
        single_shot: Callable[[int, Callable[[], None]], None],
    ) -> None:
        self._window = window
        self._single_shot = single_shot
        self._diagnostics = MainWindowPreviewDiagnosticsCoordinator(window)

    def reset_pending_sync(self) -> None:
        self._window._preview_update_pending = False
        self._window._preview_pending_pixmap = None
        self._window._pending_layer_thumbnail_ids = set()
        self._window._pending_thumbnail_flush_scheduled = False

    def on_canvas_composited_changed(self, pixmap: QPixmap) -> None:
        """Coalesce composite updates into one pending preview request.

        ``composited_changed`` never generates a preview synchronously: the
        request merges into the pending state and a single idle callback
        produces at most one preview per stroke/operation (UI-PERF-01).
        During a stroke the request only accumulates; otherwise at most one
        idle flush is scheduled.
        """
        self._window._preview_update_pending = True
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._window._preview_pending_pixmap = pixmap
        if getattr(self._window, "_is_canvas_stroke_in_progress", False):
            self.record_canvas_performance_diagnostic("preview_deferred", 0.0)
            return
        self.schedule_pending_thumbnail_flush()

    def on_preview_zoom_changed(self, zoom: float) -> None:
        """Apply preview zoom changes to the canvas viewport."""
        canvas = getattr(self._window, "canvas", None)
        if canvas is None:
            return
        viewport = getattr(canvas, "viewport", None)
        if viewport is None or not hasattr(viewport, "zoom_to"):
            return
        with suppress(Exception):
            viewport.zoom_to(float(zoom))
        self.sync_preview_viewport_indicator()

    def on_preview_fit_requested(self) -> None:
        """Fit the canvas to the current view area."""
        canvas = getattr(self._window, "canvas", None)
        if canvas is None:
            return
        fit_method = getattr(canvas, "fit_to_window", None)
        if callable(fit_method):
            with suppress(Exception):
                fit_method()
        self.sync_preview_viewport_indicator()

    def on_preview_reset_zoom_requested(self) -> None:
        """Set canvas zoom to 100%."""
        self.on_preview_zoom_changed(1.0)

    def on_preview_drag_requested(self, delta_x: float, delta_y: float) -> None:
        """Pan the canvas by dragging the preview."""
        context = self._preview_drag_context()
        if context is None:
            return
        canvas, viewport, image = context
        scale = self._preview_drag_scale(canvas, image)
        if scale is None:
            return
        scale_x, scale_y = scale
        with suppress(Exception):
            viewport.pan(-delta_x * scale_x, -delta_y * scale_y)
        self.sync_preview_viewport_indicator()

    def on_preview_image_displayed(self, _width: int, _height: int) -> None:
        """Synchronize preview viewport indicator after image updates."""
        self.sync_preview_viewport_indicator()

    def _preview_drag_context(self) -> tuple[Any, Any, Any] | None:
        canvas = getattr(self._window, "canvas", None)
        if canvas is None:
            return None
        viewport = getattr(canvas, "viewport", None)
        if viewport is None:
            return None
        preview_panel = getattr(self._window, "_preview_panel", None)
        if preview_panel is None or not hasattr(preview_panel, "get_current_image"):
            return None
        image = preview_panel.get_current_image()
        if image is None:
            return None
        return canvas, viewport, image

    def _preview_drag_scale(self, canvas: Any, image: Any) -> tuple[float, float] | None:
        state = getattr(canvas, "state", None)
        canvas_width = float(getattr(state, "width", 0.0))
        canvas_height = float(getattr(state, "height", 0.0))
        if canvas_width <= 0 or canvas_height <= 0:
            return None

        try:
            image_height, image_width = image.shape[:2]
            image_height = float(image_height)
            image_width = float(image_width)
        except Exception:
            logger.debug("_on_preview_drag_requested: suppressed exception", exc_info=True)
            return None
        if image_width <= 0 or image_height <= 0:
            return None

        scale_x = canvas_width / image_width
        scale_y = canvas_height / image_height
        if scale_x <= 0 or scale_y <= 0:
            return None
        return scale_x, scale_y

    def sync_preview_controls_from_canvas(self, state: dict[str, Any] | None = None) -> None:
        """Synchronize preview zoom controls from canvas viewport."""
        preview_panel = getattr(self._window, "_preview_panel", None)
        if preview_panel is None or not hasattr(preview_panel, "set_zoom_value"):
            return

        canvas = getattr(self._window, "canvas", None)
        viewport = getattr(canvas, "viewport", None)
        if viewport is None:
            return

        if state is None:
            try:
                state = viewport.get_state()
            except Exception:
                state = None
        if not isinstance(state, dict):
            return

        zoom = state.get("zoom")
        if not isinstance(zoom, (int, float)):
            return
        try:
            preview_panel.set_zoom_value(float(zoom))
        except Exception:
            logger.debug("Failed to sync preview zoom control from canvas", exc_info=True)

    def flush_pending_preview_sync(self) -> None:
        """Apply the latest pending preview request after stroke completion."""
        if self._canvas_transaction_active():
            # The stroke's deferred end_transaction has not recomposed yet:
            # keep the request pending without self-rescheduling — the
            # transaction's own composited_changed re-schedules this flush.
            # This bounds preview generation to at most once per stroke in
            # either timer order (UI-PERF-01 review).
            self.record_canvas_performance_diagnostic("preview_transaction_deferred", 0.0)
            return
        pending = getattr(self._window, "_preview_pending_pixmap", None)
        self._window._preview_pending_pixmap = None
        self._window._preview_update_pending = False
        self.sync_preview_from_canvas(pending)

    def _canvas_transaction_active(self) -> bool:
        host_api = getattr(self._window, "_host_api", None)
        canvas = getattr(host_api, "canvas", None) if host_api is not None else None
        return bool(getattr(canvas, "_in_transaction", False))

    def has_pending_thumbnail_flush(self) -> bool:
        if getattr(self._window, "_preview_update_pending", False):
            return True
        pending_preview = getattr(self._window, "_preview_pending_pixmap", None)
        if isinstance(pending_preview, QPixmap) and not pending_preview.isNull():
            return True
        pending_layers = getattr(self._window, "_pending_layer_thumbnail_ids", None)
        return isinstance(pending_layers, set) and bool(pending_layers)

    def schedule_pending_thumbnail_flush(self) -> None:
        self._schedule_pending_thumbnail_flush(0)

    def _schedule_pending_thumbnail_flush(self, delay_ms: int) -> None:
        if getattr(self._window, "_pending_thumbnail_flush_scheduled", False):
            return
        if not self.has_pending_thumbnail_flush():
            return
        self._window._pending_thumbnail_flush_scheduled = True
        self.record_canvas_performance_diagnostic("thumbnail_idle_flush_scheduled", 0.0)
        self._single_shot(max(0, int(delay_ms)), self._window._flush_pending_thumbnail_sync)

    def flush_pending_thumbnail_sync(self) -> None:
        self._window._pending_thumbnail_flush_scheduled = False
        if getattr(self._window, "_is_canvas_stroke_in_progress", False):
            return
        if not self.has_pending_thumbnail_flush():
            return
        start = time.perf_counter() if self.render_diagnostics_enabled() else 0.0
        pending_preview = getattr(self._window, "_preview_pending_pixmap", None)
        if getattr(self._window, "_preview_update_pending", False) or (
            isinstance(pending_preview, QPixmap) and not pending_preview.isNull()
        ):
            self.flush_pending_preview_sync()
        self.flush_pending_layer_thumbnail_sync()
        if start:
            self.record_canvas_performance_diagnostic(
                "thumbnail_idle_flush",
                (time.perf_counter() - start) * 1000.0,
            )

    def defer_layer_thumbnail_refresh(self, layer_id: str) -> None:
        if not layer_id:
            return
        pending = getattr(self._window, "_pending_layer_thumbnail_ids", None)
        if not isinstance(pending, set):
            pending = set()
            self._window._pending_layer_thumbnail_ids = pending
        pending.add(layer_id)
        self.record_canvas_performance_diagnostic("layer_thumbnail_deferred", 0.0)

    def refresh_layer_thumbnail(self, layer_id: str) -> bool:
        panel = getattr(self._window, "_layer_panel", None)
        if panel is None or not layer_id or not hasattr(panel, "refresh_thumbnail"):
            return False
        start = time.perf_counter() if self.render_diagnostics_enabled() else 0.0
        panel.refresh_thumbnail(layer_id)
        if start:
            self.record_canvas_performance_diagnostic(
                "layer_thumbnail_update",
                (time.perf_counter() - start) * 1000.0,
            )
        return True

    def flush_pending_layer_thumbnail_sync(self) -> None:
        pending = getattr(self._window, "_pending_layer_thumbnail_ids", None)
        if not isinstance(pending, set) or not pending:
            return
        layer_ids = tuple(pending)
        pending.clear()
        for layer_id in layer_ids:
            self.refresh_layer_thumbnail(layer_id)

    def render_diagnostics_enabled(self) -> bool:
        return self._diagnostics.enabled()

    def record_canvas_performance_diagnostic(self, name: str, elapsed_ms: float) -> None:
        self._diagnostics.record(name, elapsed_ms)

    def get_canvas_performance_diagnostics(self) -> dict[str, Any]:
        """Return dev/test-only canvas plus preview/page thumbnail diagnostics."""
        return self._diagnostics.snapshot()

    def sync_preview_from_canvas(self, pixmap: QPixmap | None = None) -> None:
        """Update preview with the current composited canvas image."""
        preview_panel = getattr(self._window, "_preview_panel", None)
        if preview_panel is None or not hasattr(preview_panel, "set_image_from_qpixmap"):
            return

        host_api = getattr(self._window, "_host_api", None)
        canvas = host_api.canvas if host_api else None
        if getattr(canvas, "_gpu_rendering", False):
            if self._defer_gpu_preview_readback_if_needed(pixmap):
                self.sync_preview_viewport_indicator()
                return
            handled, native_pixmap = self._native_gpu_preview_pixmap()
            if handled and (native_pixmap is None or native_pixmap.isNull()):
                # The dedicated low-resolution preview readback is
                # momentarily unavailable: keep the last preview and let
                # the existing idle/coalesce retries refresh it.  Live
                # updates never fall back to the full-resolution export
                # readback (UI-PERF-01).
                self.sync_preview_viewport_indicator()
                return
            pixmap = native_pixmap if handled else None

        source_pixmap = (
            pixmap
            if isinstance(pixmap, QPixmap) and not pixmap.isNull()
            else self._live_composited_pixmap_fallback()
        )
        if source_pixmap is not None and not source_pixmap.isNull():
            preview_start = time.perf_counter() if self.render_diagnostics_enabled() else 0.0
            preview_panel.set_image_from_qpixmap(source_pixmap)
            if preview_start:
                self.record_canvas_performance_diagnostic(
                    "preview_update",
                    (time.perf_counter() - preview_start) * 1000.0,
                )
            self.update_active_page_thumbnail_from_preview(source_pixmap)
        elif hasattr(preview_panel, "clear_preview"):
            preview_panel.clear_preview()

        self._ensure_legacy_preview_image(preview_panel)
        self.sync_preview_viewport_indicator()

    def _native_gpu_preview_pixmap(self) -> tuple[bool, QPixmap | None]:
        """Return ``(handled, pixmap)`` for the wgpu-native preview path.

        ``handled`` is True when the native surface is active and exposes
        the dedicated low-resolution preview API; the caller then must not
        touch any full-resolution readback for this live update.
        """

        canvas_widget = getattr(self._window, "canvas", None)
        if not bool(getattr(canvas_widget, "native_surface_active", False)):
            return False, None
        getter = getattr(canvas_widget, "get_gpu_preview_pixmap", None)
        if not callable(getter):
            return False, None
        try:
            return True, getter()
        except Exception:
            logger.debug("native preview readback failed; keeping last preview", exc_info=True)
            return True, None

    def _live_composited_pixmap_fallback(self) -> QPixmap | None:
        """Legacy composited pixmap for preview when no native preview exists.

        When the native surface is active this would reach the export
        readback — that must never happen from live preview, so tally it
        for the ``live_export_readback_count`` invariant (== 0).
        """

        canvas_widget = getattr(self._window, "canvas", None)
        if bool(getattr(canvas_widget, "native_surface_active", False)):
            diagnostics = getattr(self._window, "_canvas_performance_diagnostics", None)
            if not isinstance(diagnostics, dict):
                diagnostics = {}
                self._window._canvas_performance_diagnostics = diagnostics
            diagnostics["live_export_readback_count"] = (
                int(diagnostics.get("live_export_readback_count", 0)) + 1
            )
        return self._window._get_composited_pixmap()

    def _defer_gpu_preview_readback_if_needed(self, pixmap: QPixmap | None) -> bool:
        canvas_widget = getattr(self._window, "canvas", None)
        should_defer = getattr(canvas_widget, "should_defer_gpu_preview_readback", None)
        if not callable(should_defer) or not bool(should_defer()):
            return False
        self.record_canvas_performance_diagnostic("preview_readback_deferred", 0.0)
        self._window._preview_update_pending = True
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._window._preview_pending_pixmap = pixmap
        request_refresh = getattr(canvas_widget, "request_native_surface_refresh", None)
        if callable(request_refresh):
            try:
                request_refresh(force_full_recompose=True)
            except Exception:
                logger.debug("Failed to request native preview readback warmup", exc_info=True)
        if self.has_pending_thumbnail_flush():
            self._schedule_pending_thumbnail_flush(_PREVIEW_READBACK_RETRY_MS)
        return True

    def _ensure_legacy_preview_image(self, preview_panel: Any) -> None:
        if not hasattr(preview_panel, "get_current_image"):
            return
        if preview_panel.get_current_image() is not None:
            return

        host_api = getattr(self._window, "_host_api", None)
        canvas = host_api.canvas if host_api else None
        if canvas is None or not hasattr(canvas, "get_composite_image"):
            return
        try:
            composite = canvas.get_composite_image()
            if isinstance(composite, np.ndarray) and composite.ndim == 3 and composite.size > 0:
                preview_start = time.perf_counter() if self.render_diagnostics_enabled() else 0.0
                preview_panel.set_image(composite)
                if preview_start:
                    self.record_canvas_performance_diagnostic(
                        "preview_update",
                        (time.perf_counter() - preview_start) * 1000.0,
                    )
        except Exception:
            logger.debug("Failed to fetch legacy composite image for preview", exc_info=True)

    def update_active_page_thumbnail_from_preview(self, pixmap: QPixmap) -> None:
        host_api = getattr(self._window, "_host_api", None)
        pages = getattr(host_api, "pages", None) if host_api is not None else None
        update_thumbnail = getattr(pages, "update_active_page_thumbnail", None)
        if not callable(update_thumbnail):
            return
        encode_start = time.perf_counter() if self.render_diagnostics_enabled() else 0.0
        thumbnail_png = self.encode_page_thumbnail_png(pixmap)
        if encode_start:
            self.record_canvas_performance_diagnostic(
                "page_thumbnail_encode",
                (time.perf_counter() - encode_start) * 1000.0,
            )
        if thumbnail_png is None:
            return
        try:
            update_start = time.perf_counter() if self.render_diagnostics_enabled() else 0.0
            update_thumbnail(thumbnail_png)
            if update_start:
                self.record_canvas_performance_diagnostic(
                    "page_thumbnail_update",
                    (time.perf_counter() - update_start) * 1000.0,
                )
        except Exception:
            logger.debug("Failed to update active page thumbnail", exc_info=True)

    def encode_page_thumbnail_png(self, pixmap: QPixmap) -> bytes | None:
        if pixmap.isNull():
            return None
        scaled = pixmap.scaled(
            256,
            256,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            return None
        try:
            if not scaled.save(buffer, "PNG"):
                return None
            return bytes(cast(Any, buffer.data()))
        finally:
            buffer.close()

    def sync_preview_viewport_indicator(self) -> None:
        preview_panel = getattr(self._window, "_preview_panel", None)
        if preview_panel is None:
            return

        canvas = getattr(self._window, "canvas", None)
        if canvas is None or not hasattr(preview_panel, "set_viewport_indicator"):
            preview_panel.set_viewport_indicator(None)
            return

        viewport = getattr(canvas, "viewport", None)
        if viewport is None or not hasattr(viewport, "get_visible_rect"):
            preview_panel.set_viewport_indicator(None)
            return

        image = preview_panel.get_current_image()
        if image is None:
            preview_panel.set_viewport_indicator(None)
            return

        state = getattr(canvas, "state", None)
        canvas_width = float(getattr(state, "width", 0.0))
        canvas_height = float(getattr(state, "height", 0.0))
        if canvas_width <= 0 or canvas_height <= 0:
            preview_panel.set_viewport_indicator(None)
            return

        current_zoom = float(getattr(viewport.state, "zoom", 1.0))
        if not isfinite(current_zoom) or current_zoom <= 1.0:
            preview_panel.set_viewport_indicator(None)
            return

        self._set_viewport_indicator_from_image(
            preview_panel, viewport, image, canvas_width, canvas_height
        )

    def _set_viewport_indicator_from_image(
        self,
        preview_panel: Any,
        viewport: Any,
        image: Any,
        canvas_width: float,
        canvas_height: float,
    ) -> None:
        try:
            image_height_i, image_width_i = image.shape[:2]
            image_width = float(image_width_i)
            image_height = float(image_height_i)
        except Exception:
            logger.debug("_sync_preview_viewport_indicator: suppressed exception", exc_info=True)
            preview_panel.set_viewport_indicator(None)
            return

        if image_width <= 0 or image_height <= 0:
            preview_panel.set_viewport_indicator(None)
            return

        scale_x = image_width / canvas_width
        scale_y = image_height / canvas_height
        if scale_x <= 0 or scale_y <= 0:
            preview_panel.set_viewport_indicator(None)
            return

        visible_rect = viewport.get_visible_rect()
        if visible_rect.isEmpty():
            preview_panel.set_viewport_indicator(None)
            return

        mapped_rect = QRectF(
            visible_rect.x() * scale_x,
            visible_rect.y() * scale_y,
            visible_rect.width() * scale_x,
            visible_rect.height() * scale_y,
        )
        scene_bounds = QRectF(0.0, 0.0, float(image_width), float(image_height))
        mapped_rect = mapped_rect.intersected(scene_bounds)
        if mapped_rect.isEmpty():
            preview_panel.set_viewport_indicator(None)
            return

        preview_panel.set_viewport_indicator(mapped_rect)
