"""Widget host that separates native canvas presentation from overlays."""

from __future__ import annotations

import ctypes
import logging
import math
import os
import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QHideEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QShowEvent,
    QTabletEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QWidget

from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.ui._dock_safe import is_valid as _is_qt_object_valid

from .binding_danger_overlay import draw_binding_danger
from .canvas_native_lifecycle_coordinator import (
    CanvasViewportNativeLifecycleCoordinator,
    NativeVisualProbeCoordinator,
)
from .canvas_widget import CanvasWidget, _select_render_backend
from .render_scene import (
    RenderLayerSnapshot,
    build_render_scene_snapshot,
    vector_raster_scale_bucket,
    view_resolution_compositing_enabled,
)
from .spread_frame_overlay import draw_spread_frames
from .wgpu_backend import WgpuBackendError, WgpuDeviceLostError, WgpuUnavailableError
from .wgpu_native_canvas_presenter import (
    NATIVE_SMOKE_CLEAR_RGBA,
    WgpuNativeCanvasPresenter,
)

logger = logging.getLogger(__name__)
_NATIVE_VISUAL_PROBE_TRANSIENT_MISMATCH = "live native surface capture did not match smoke frame"
_NATIVE_VISUAL_PROBE_NOT_READY = "live native surface was not ready for visual probe"
_NATIVE_VISUAL_PROBE_DEFERRED_RETRIES = 12
_NATIVE_VISUAL_PROBE_CAPTURE_ATTEMPTS = 4
_NATIVE_VISUAL_PROBE_CAPTURE_SETTLE_SECONDS = 0.016


class _NativeSurfaceWidget(QWidget):
    """Native child widget whose HWND is owned by wgpu."""

    fallback_requested = Signal(str)

    def __init__(self, host: CanvasViewportHost) -> None:
        super().__init__(host)
        self._host = host
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        # Hover reaches this native child, not the host's mouse-tracking flag.
        self.setMouseTracking(True)

    def paintEngine(self) -> Any:  # noqa: N802
        return None

    def paintEvent(self, event: Any) -> None:
        del event
        if not self._host.native_surface_active:
            return
        if not self._host.native_visual_probe_ready:
            self._host._schedule_native_visual_probe()
            return
        reason = self._host._present_native_surface()
        if reason is not None:
            self.fallback_requested.emit(reason)
            return
        self._host._repaint_native_overlay_surface()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._host._forward_mouse_event_to_legacy(event, "mousePressEvent", source=self)

    def enterEvent(self, event: Any) -> None:  # noqa: N802
        super().enterEvent(event)
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return
        hover = QMouseEvent(
            QEvent.Type.MouseMove,
            event.position(),
            event.globalPosition(),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            QApplication.keyboardModifiers(),
        )
        self._host._forward_mouse_event_to_legacy(hover, "mouseMoveEvent", source=self)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._host._forward_mouse_event_to_legacy(event, "mouseMoveEvent", source=self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._host._forward_mouse_event_to_legacy(event, "mouseReleaseEvent", source=self)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self._host._forward_mouse_event_to_legacy(
            event,
            "mouseDoubleClickEvent",
            source=self,
        )

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._host._forward_wheel_event_to_legacy(event, source=self)

    def tabletEvent(self, event: QTabletEvent) -> None:
        self._host._forward_tablet_event_to_legacy(event, source=self)


class _OverlaySurfaceWidget(QWidget):
    """Transparent Qt overlay drawn above the native wgpu surface."""

    def __init__(self, host: CanvasViewportHost) -> None:
        super().__init__(host)
        self._host = host
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        transparent_input = getattr(Qt.WindowType, "WindowTransparentForInput", None)
        if transparent_input is not None:
            flags |= transparent_input
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)

    def paintEvent(self, event: Any) -> None:
        del event
        if not self._host.native_surface_active:
            return
        legacy = self._host.legacy_canvas
        canvas = legacy._get_canvas_service()
        painter = QPainter(self)
        try:
            viewport_offset = self._host._native_overlay_viewport_offset()
            canvas_transform = self._host._native_overlay_canvas_transform(
                legacy.viewport.get_transform(),
                viewport_offset,
            )
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            if canvas is None:
                self._host._paint_canvas_readiness_gate(painter)
                return
            overlay_start = self._host._overlay_timer_start()
            legacy._draw_selection_preview(
                painter,
                canvas,
                canvas_transform=canvas_transform,
            )
            painter.setTransform(canvas_transform)
            legacy._draw_mask_overlays(painter, canvas)
            painter.resetTransform()

            try:
                flat_layers = list(self._host.native_overlay_flat_layers)
            except Exception:
                flat_layers = []
                logger.debug("native overlay snapshot failed", exc_info=True)

            if legacy._overlay_enabled:
                dpr = legacy.devicePixelRatioF()
                output_offset = (
                    float(viewport_offset.x()),
                    float(viewport_offset.y()),
                )
                legacy._overlay_manager.render_eligible_layers(
                    painter,
                    canvas,
                    legacy.viewport,
                    dpr,
                    flat=flat_layers,
                    output_offset=output_offset,
                    canvas_transform=canvas_transform,
                )
                legacy._overlay_manager.render_preview(
                    painter,
                    canvas,
                    legacy.viewport,
                    dpr,
                    flat=flat_layers,
                    output_offset=output_offset,
                    canvas_transform=canvas_transform,
                )

            if legacy.state.antialiasing:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setTransform(canvas_transform)
            legacy._draw_overlays(painter)
            self._host._paint_spread_frames(painter)
            self._host._paint_canvas_readiness_gate(painter)
            self._host._paint_native_cmyk_pending(painter)
            self._host._record_overlay_paint_ms(overlay_start)
        finally:
            painter.end()


class CanvasViewportHost(QWidget):
    """Canvas host for the native wgpu surface migration.

    The host keeps :class:`CanvasWidget` as the compatibility controller and
    bitmap fallback, while normal native presentation happens in a plain child
    ``QWidget`` that wgpu can own as a swapchain surface.
    """

    stroke_started = Signal(object)
    stroke_updated = Signal(list)
    stroke_ended = Signal(object)
    pointer_hovered = Signal(object)
    viewport_changed = Signal(dict)
    canvas_resized = Signal(int, int)
    context_menu_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Give the swapchain child a stable native parent before creating it.
        # Promoting an alien ancestor later (e.g. during docking) can make Qt
        # add that ancestor's position twice to the child's native origin,
        # even while the child's QWidget.pos() remains (0, 0).
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.legacy_canvas = CanvasWidget(self)
        self.legacy_canvas.hide()
        self._native_surface = _NativeSurfaceWidget(self)
        self._overlay_surface = _OverlaySurfaceWidget(self)
        self.legacy_canvas._guide_viewport_host = self
        self._native_surface.fallback_requested.connect(self._activate_legacy_fallback)
        self._native_presenter: WgpuNativeCanvasPresenter | None = None
        self._native_disabled_reason: str | None = None
        self._native_fallback_count = 0
        self._native_device_lost_count = 0
        self._native_visual_probe_passed = False
        self._native_visual_probe_pending = False
        self._native_visual_probe_running = False
        self._native_visual_probe_retry_count = 0
        self._last_native_visual_probe_status = ""
        self._last_native_visual_probe_reason: str | None = None
        self._native_live_surface_capture_supported = False
        self._native_scene_presented_after_probe = False
        self._native_overlay_flat_layers: tuple[RenderLayerSnapshot, ...] = ()
        self._native_present_pending = False
        self._native_force_next_present = False
        self._native_flush_after_present = False
        self._canvas_readiness_gate_active = False
        self._canvas_readiness_gate_message = ""
        self._canvas_readiness_gate_previous_legacy_enabled = True
        self._native_shutting_down = False
        self._soft_proof_lut: Any | None = None
        self._spread_controller: Any | None = None
        self._overlay_event_root: QWidget | None = None
        self._overlay_geometry_sync_timer = QTimer(self)
        self._overlay_geometry_sync_timer.setSingleShot(True)
        self._overlay_geometry_sync_timer.timeout.connect(self._sync_child_geometry)
        self._native_requested = (
            _select_render_backend(os.environ.get("LOOMSTROKE_RENDER_BACKEND")) == "wgpu_native"
        )
        self._native_active = bool(self._native_requested)
        self._native_lifecycle_coordinator = CanvasViewportNativeLifecycleCoordinator(
            self,
            presenter_factory=self._create_native_presenter_for_lifecycle,
            timer_single_shot=lambda delay_ms, callback: QTimer.singleShot(delay_ms, callback),
            unavailable_error_type=WgpuUnavailableError,
            backend_error_type=WgpuBackendError,
            not_ready_reason=_NATIVE_VISUAL_PROBE_NOT_READY,
            transient_mismatch_reason=_NATIVE_VISUAL_PROBE_TRANSIENT_MISMATCH,
            deferred_retries=_NATIVE_VISUAL_PROBE_DEFERRED_RETRIES,
            capture_attempts=_NATIVE_VISUAL_PROBE_CAPTURE_ATTEMPTS,
            capture_settle_seconds=_NATIVE_VISUAL_PROBE_CAPTURE_SETTLE_SECONDS,
            smoke_clear_rgba=NATIVE_SMOKE_CLEAR_RGBA,
        )

        self.viewport = self.legacy_canvas.viewport
        self.input_handler = self.legacy_canvas.input_handler
        self.input_handler.canvas = self
        self.state = self.legacy_canvas.state

        self._relay_legacy_signals()
        self.setMouseTracking(True)
        self.setTabletTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setAcceptDrops(True)
        self.setMinimumSize(self.legacy_canvas.minimumSize())
        self._sync_child_geometry()
        self._apply_surface_visibility()

    @property
    def native_surface_active(self) -> bool:
        return (
            self._native_active
            and self._native_disabled_reason is None
            and not self._native_shutting_down
        )

    @property
    def native_visual_probe_ready(self) -> bool:
        return self._native_visual_probe_passed

    @property
    def native_overlay_flat_layers(self) -> tuple[RenderLayerSnapshot, ...]:
        return self._native_overlay_flat_layers

    @property
    def input_handler(self) -> Any:
        return self.legacy_canvas.input_handler

    @input_handler.setter
    def input_handler(self, handler: Any) -> None:
        self.legacy_canvas.input_handler = handler
        if hasattr(handler, "canvas"):
            handler.canvas = self

    def __getattr__(self, name: str) -> Any:
        return getattr(self.legacy_canvas, name)

    def update(self, *args: Any) -> None:
        if self.native_surface_active:
            self._native_surface.update(*args)
            # The transparent Qt overlay repaints selection, tool chrome, and
            # screen-space vector images over the native child surface.  Passing
            # CanvasWidget's stroke dirty rect through here clips Qt's paint
            # region, leaving overlay pixels missing outside that rect.
            self._overlay_surface.update()
        else:
            self.legacy_canvas.update(*args)
        super().update(*args)

    def setCursor(self, cursor: Any) -> None:  # noqa: N802
        """Apply the active tool cursor to every surface that can receive input."""

        super().setCursor(cursor)
        if _is_qt_object_valid(self._native_surface):
            self._native_surface.setCursor(cursor)
        if _is_qt_object_valid(self._overlay_surface):
            self._overlay_surface.setCursor(cursor)
        if _is_qt_object_valid(self.legacy_canvas):
            self.legacy_canvas.setCursor(cursor)

    def unsetCursor(self) -> None:  # noqa: N802
        """Clear the active tool cursor from the host and child surfaces."""

        super().unsetCursor()
        if _is_qt_object_valid(self._native_surface):
            self._native_surface.unsetCursor()
        if _is_qt_object_valid(self._overlay_surface):
            self._overlay_surface.unsetCursor()
        if _is_qt_object_valid(self.legacy_canvas):
            self.legacy_canvas.unsetCursor()

    def async_initialize(self) -> None:
        self.legacy_canvas.async_initialize()

    def close_document_resources(self) -> None:
        """Release both presenters and overlays without retiring the host."""
        self._release_native_presenter()
        self.legacy_canvas.close_document_resources()

    def async_cleanup(self) -> None:
        self._prepare_native_surface_shutdown()
        self.legacy_canvas.async_cleanup()

    def _prepare_native_surface_shutdown(self) -> None:
        self._native_shutting_down = True
        self._native_active = False
        self._native_requested = False
        self._overlay_geometry_sync_timer.stop()
        self._remove_overlay_root_event_filter()
        if _is_qt_object_valid(self._overlay_surface):
            self._overlay_surface.hide()
        self._release_native_presenter()

    def deleteLater(self) -> None:  # noqa: N802
        if not self._native_shutting_down:
            self._prepare_native_surface_shutdown()
        super().deleteLater()

    def set_host_api(self, host_api: Any | None) -> None:
        self.legacy_canvas.set_host_api(host_api)
        if host_api and hasattr(host_api.canvas, "composited_changed"):
            try:
                host_api.canvas.composited_changed.connect(self._on_composited_changed)
            except Exception:
                logger.debug("native canvas host signal connect failed", exc_info=True)
        if host_api:
            canvas = getattr(host_api, "canvas", None)
            set_gpu_rendering = getattr(canvas, "set_gpu_rendering", None)
            if callable(set_gpu_rendering):
                set_gpu_rendering(True)

    def set_guide_controller(self, controller: Any | None) -> None:
        self.legacy_canvas.set_guide_controller(controller)

    def set_spread_controller(self, controller: Any | None) -> None:
        """Attach the spread viewport state (CT-06 B4-c4).

        Only the native surface draws the facing page; the bitmap
        compatibility path stays single-page.
        """
        self._spread_controller = controller
        self._request_native_present(force_full_recompose=True)

    def set_context_toolbar_manager(self, manager: Any) -> None:
        self.legacy_canvas.set_context_toolbar_manager(manager)

    def set_overlay_enabled(self, enabled: bool) -> None:
        self.legacy_canvas.set_overlay_enabled(enabled)
        self._request_native_present(force_full_recompose=True)
        self._overlay_surface.update()

    def set_overlay_max_dim(self, max_dim: int) -> None:
        self.legacy_canvas.set_overlay_max_dim(max_dim)
        self._request_native_present(force_full_recompose=True)
        self._overlay_surface.update()

    def set_overlay_debug_timing(self, enabled: bool) -> None:
        self.legacy_canvas.set_overlay_debug_timing(enabled)

    def set_soft_proof_lut(self, lut: Any | None) -> None:
        """Session-only soft proof final pass (CT-11 P2c, spec 11 §8.2).

        The LUT lives on the host so a presenter recreated after device
        loss re-applies it.  Display only — export never sees it.
        """

        self._soft_proof_lut = lut
        if self._native_presenter is not None:
            self._native_presenter.set_soft_proof_lut(lut)
        # Same-scene re-present: only the final pass changed.
        self.request_native_surface_refresh()
        self._overlay_surface.update()

    @property
    def soft_proof_lut(self) -> Any | None:
        return self._soft_proof_lut

    def request_native_surface_refresh(self, *, force_full_recompose: bool = False) -> None:
        """Refresh the native child surface after Qt overlay widgets move.

        This is used for UI chrome that floats over the canvas.  The canvas
        scene may be unchanged, but the surface still needs another present
        to clear stale pixels below the old widget rectangle.
        """

        if self.native_surface_active:
            self._native_flush_after_present = True
            if not force_full_recompose and self._native_presenter is not None:
                self._native_presenter.force_next_surface_present()
            self._request_native_present(force_full_recompose=force_full_recompose)
            self._native_surface.update()
            return
        self.legacy_canvas.update()

    def set_tool_preview(
        self,
        image: QImage | None,
        composition_mode: QPainter.CompositionMode | None = None,
        opacity: float = 1.0,
    ) -> None:
        self.legacy_canvas.set_tool_preview(image, composition_mode, opacity)
        self._overlay_surface.update()

    def set_tool_overlay_fn(self, fn: Any) -> None:
        self.legacy_canvas.set_tool_overlay_fn(fn)
        self._overlay_surface.update()

    def set_canvas_agent_overlay_fn(self, fn: Any) -> None:
        self.legacy_canvas.set_canvas_agent_overlay_fn(fn)
        self._overlay_surface.update()

    def is_gpu_enabled(self) -> bool:
        if self.native_surface_active:
            return True
        return self.legacy_canvas.is_gpu_enabled()

    def get_gpu_renderer_name(self) -> str:
        if self._native_presenter is not None:
            adapter = self._native_presenter.diagnostics.adapter_name
            if adapter:
                return adapter
        return self.legacy_canvas.get_gpu_renderer_name()

    def get_render_diagnostics(self) -> dict[str, Any]:
        info = self.legacy_canvas.get_render_diagnostics()
        diagnostics = (
            self._native_presenter.diagnostics
            if self._native_presenter is not None and self.native_surface_active
            else None
        )
        if diagnostics is not None:
            info.update(
                {
                    "backend_name": diagnostics.backend_name,
                    "adapter_name": diagnostics.adapter_name,
                    "render_frame_ms": diagnostics.render_frame_ms,
                    "readback_ms": diagnostics.readback_ms,
                    "scene_build_ms": diagnostics.scene_build_ms,
                    "scene_flatten_ms": diagnostics.scene_flatten_ms,
                    "overlay_paint_ms": diagnostics.overlay_paint_ms,
                    "present_ms": diagnostics.present_ms,
                    "upload_ms": diagnostics.upload_ms,
                    "export_readback_ms": diagnostics.export_readback_ms,
                    "dirty_rect_size": diagnostics.dirty_rect_size,
                    "damage_rect_size": diagnostics.damage_rect_size,
                    "surface_status": diagnostics.surface_status,
                    "surface_size": diagnostics.surface_size,
                    "surface_dpr": diagnostics.surface_dpr,
                    "surface_recreate_count": diagnostics.surface_recreate_count,
                    "surface_lost_count": diagnostics.surface_lost_count,
                    "fallback_tier": diagnostics.fallback_tier,
                    "fallback_reason": diagnostics.fallback_reason,
                    "fallback_to_bitmap_count": max(
                        diagnostics.fallback_to_bitmap_count,
                        self._native_fallback_count,
                    ),
                    "emergency_gl_count": 0,
                    "full_frame_fallback_count": diagnostics.full_frame_fallback_count,
                    "layers_seen": diagnostics.layers_seen,
                    "layers_drawn": diagnostics.layers_drawn,
                    "full_uploads": diagnostics.full_uploads,
                    "partial_uploads": diagnostics.partial_uploads,
                    "partial_upload_fallbacks": diagnostics.partial_upload_fallbacks,
                    "uploaded_layer_ids": diagnostics.uploaded_layer_ids,
                    "full_upload_layer_ids": diagnostics.full_upload_layer_ids,
                    "partial_upload_layer_ids": diagnostics.partial_upload_layer_ids,
                    "partial_upload_fallback_layer_ids": (
                        diagnostics.partial_upload_fallback_layer_ids
                    ),
                    "device_lost_count": max(
                        diagnostics.device_lost_count,
                        self._native_device_lost_count,
                    ),
                    "fallback_to_gl_count": 0,
                    "texture_cache_estimate": diagnostics.texture_cache_estimate,
                    "present_p95_ms": diagnostics.present_p95_ms,
                    "last_present_serial": diagnostics.last_present_serial,
                    "last_scene_signature": diagnostics.last_scene_signature,
                    "last_visual_probe_status": diagnostics.last_visual_probe_status,
                    "last_visual_probe_reason": diagnostics.last_visual_probe_reason,
                    "live_surface_capture_supported": (diagnostics.live_surface_capture_supported),
                }
            )
            preview_diagnostics = getattr(self._native_presenter, "preview_diagnostics", None)
            if isinstance(preview_diagnostics, dict):
                info.update(preview_diagnostics)
        elif self._native_requested:
            legacy_tier = str(info.get("fallback_tier") or "")
            if not legacy_tier:
                legacy_tier = (
                    "wgpu_bitmap"
                    if getattr(self.legacy_canvas, "_wgpu_bitmap_disabled_reason", None) is None
                    else "wgpu_unavailable"
                )
            info.update(
                {
                    "fallback_tier": legacy_tier,
                    "fallback_reason": self._native_disabled_reason,
                    "fallback_to_bitmap_count": self._native_fallback_count,
                    "emergency_gl_count": 0,
                    "last_visual_probe_status": self._last_native_visual_probe_status,
                    "last_visual_probe_reason": self._last_native_visual_probe_reason,
                    "live_surface_capture_supported": (self._native_live_surface_capture_supported),
                }
            )
        info.update(
            {
                "selected_backend": (
                    "wgpu_native" if self._native_requested else info.get("selected_backend")
                ),
                "wgpu_native_requested": self._native_requested,
                "wgpu_native_disabled_reason": self._native_disabled_reason,
                "wgpu_native_host_geometry": self._widget_geometry_diagnostics(self),
                "wgpu_native_surface_geometry": self._widget_geometry_diagnostics(
                    self._native_surface
                ),
                "wgpu_native_overlay_geometry": self._widget_geometry_diagnostics(
                    self._overlay_surface
                ),
                "wgpu_native_overlay_is_window": (
                    _is_qt_object_valid(self._overlay_surface) and self._overlay_surface.isWindow()
                ),
            }
        )
        return info

    @staticmethod
    def _widget_geometry_diagnostics(widget: QWidget) -> dict[str, Any]:
        if not _is_qt_object_valid(widget):
            return {"valid": False}
        try:
            global_pos = widget.mapToGlobal(QPoint(0, 0))
        except RuntimeError:
            global_pos = QPoint(0, 0)
        geometry = widget.geometry()
        return {
            "valid": True,
            "geometry": (
                int(geometry.x()),
                int(geometry.y()),
                int(geometry.width()),
                int(geometry.height()),
            ),
            "global_pos": (int(global_pos.x()), int(global_pos.y())),
            "size": (int(widget.width()), int(widget.height())),
            "visible": bool(widget.isVisible()),
            "is_window": bool(widget.isWindow()),
        }

    def flush_and_readback(self) -> QPixmap | None:
        if self.native_surface_active:
            reason = self._present_native_surface_for_readback()
            if reason is not None:
                if reason != _NATIVE_VISUAL_PROBE_NOT_READY:
                    self._activate_legacy_fallback(reason)
            else:
                pixmap = self._get_wgpu_native_pixmap_for_export()
                if pixmap is not None:
                    return pixmap
        return self.legacy_canvas.flush_and_readback()

    def get_gpu_composited_pixmap(self) -> QPixmap | None:
        pixmap = self._get_wgpu_native_pixmap_for_export()
        if pixmap is not None:
            return pixmap
        return self.legacy_canvas.get_gpu_composited_pixmap()

    def _get_wgpu_native_pixmap_for_export(self) -> QPixmap | None:
        if not self.native_surface_active or self._native_presenter is None:
            return None
        image = self._native_presenter.readback_for_export()
        if image is None or image.isNull():
            return None
        return QPixmap.fromImage(image)

    def get_gpu_preview_pixmap(self, max_extent: int = 256) -> QPixmap | None:
        """Low-resolution live preview readback (UI-PERF-01).

        Never the export path: the preview renderer composites at most
        ``max_extent`` px per side through the shared native compositor.
        ``None`` means "keep the last preview" — callers must not fall back
        to ``flush_and_readback`` / ``readback_for_export`` for live updates.
        """

        if not self.native_surface_active or self._native_presenter is None:
            return None
        readback = getattr(self._native_presenter, "readback_for_preview", None)
        if not callable(readback):
            return None
        try:
            # Preview may flush before the queued native paint (e.g. Undo).
            # Synchronize the scene without forcing projection/texture rebuilds.
            if self._present_native_surface() is not None:
                return None
            image = readback(max_extent=max_extent)
        except Exception:
            logger.exception("get_gpu_preview_pixmap: native preview readback failed")
            return None
        if image is None or image.isNull():
            return None
        return QPixmap.fromImage(image)

    def _present_native_surface_for_readback(self) -> str | None:
        return self._native_lifecycle().present_for_readback()

    def should_defer_gpu_preview_readback(self) -> bool:
        """Avoid preview readback until the native display path is warm.

        Preview/page thumbnails can retry shortly; forcing the hidden legacy
        bitmap presenter here would make the first user stroke pay the slow
        compatibility readback cost.
        """

        if not self.native_surface_active:
            return False
        return not (
            self._native_visual_probe_passed
            and self._native_presenter is not None
            and self._native_scene_presented_after_probe
        )

    @property
    def canvas_readiness_gate_active(self) -> bool:
        return self._canvas_readiness_gate_active

    def set_canvas_readiness_gate_active(
        self,
        active: bool,
        message: str | None = None,
    ) -> None:
        """Show a short startup gate while native presentation warms up."""

        new_active = bool(active)
        if new_active and not self._canvas_readiness_gate_active:
            self._canvas_readiness_gate_previous_legacy_enabled = bool(
                self.legacy_canvas.isEnabled()
            )
            self.legacy_canvas.setEnabled(False)
        elif not new_active and self._canvas_readiness_gate_active:
            self.legacy_canvas.setEnabled(self._canvas_readiness_gate_previous_legacy_enabled)

        self._canvas_readiness_gate_active = new_active
        self._canvas_readiness_gate_message = str(message or "") if new_active else ""
        if self.native_surface_active:
            self._sync_child_geometry()
            if self._overlay_surface.isVisible():
                self._overlay_surface.raise_()
            self._overlay_surface.update()
            self._native_surface.update()
        else:
            self.legacy_canvas.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_child_geometry()
        self.legacy_canvas.resize(event.size())
        self.legacy_canvas.viewport.set_widget_size(event.size().width(), event.size().height())
        self.legacy_canvas._overlay_manager.set_widget_size(
            event.size().width(),
            event.size().height(),
        )
        self._request_native_present(force_full_recompose=True)

    def moveEvent(self, event: Any) -> None:
        super().moveEvent(event)
        self._sync_child_geometry()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._sync_child_geometry()
        self._apply_surface_visibility()
        self._request_native_present(force_full_recompose=True)

    def hideEvent(self, event: QHideEvent) -> None:
        if _is_qt_object_valid(self._overlay_surface):
            self._overlay_surface.hide()
        self._release_native_presenter()
        super().hideEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        dpr_event = getattr(QEvent.Type, "DevicePixelRatioChange", None)
        screen_event = getattr(QEvent.Type, "ScreenChangeInternal", None)
        window_state_event = getattr(QEvent.Type, "WindowStateChange", None)
        if event.type() in {dpr_event, screen_event, window_state_event}:
            self._release_native_presenter()
            self._sync_child_geometry()
            self.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self._overlay_event_root
            and event.type()
            in {
                QEvent.Type.Move,
                QEvent.Type.Resize,
                QEvent.Type.Show,
                QEvent.Type.WindowStateChange,
            }
            and _is_qt_object_valid(self)
            and not self._overlay_geometry_sync_timer.isActive()
        ):
            self._overlay_geometry_sync_timer.start(0)
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._forward_mouse_event_to_legacy(event, "mousePressEvent", source=self)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._forward_mouse_event_to_legacy(event, "mouseMoveEvent", source=self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._forward_mouse_event_to_legacy(event, "mouseReleaseEvent", source=self)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self._forward_mouse_event_to_legacy(event, "mouseDoubleClickEvent", source=self)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._forward_wheel_event_to_legacy(event, source=self)

    def tabletEvent(self, event: QTabletEvent) -> None:
        self._forward_tablet_event_to_legacy(event, source=self)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._consume_input_if_canvas_readiness_gate(event):
            return
        self.legacy_canvas.keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if self._consume_input_if_canvas_readiness_gate(event):
            return
        self.legacy_canvas.keyReleaseEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._consume_input_if_canvas_readiness_gate(event):
            return
        self.legacy_canvas.dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._consume_input_if_canvas_readiness_gate(event):
            return
        self.legacy_canvas.dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if self._consume_input_if_canvas_readiness_gate(event):
            return
        self.legacy_canvas.dropEvent(event)

    def request_context_menu(self, widget_pos: tuple[float, float]) -> None:
        pos = QPoint(int(widget_pos[0]), int(widget_pos[1]))
        canvas_pos = self.viewport.widget_to_canvas(widget_pos[0], widget_pos[1])
        payload = {
            "global_pos": self.mapToGlobal(pos),
            "widget_pos": (float(widget_pos[0]), float(widget_pos[1])),
            "canvas_pos": (float(canvas_pos[0]), float(canvas_pos[1])),
        }
        self.context_menu_requested.emit(payload)

    @staticmethod
    def _point_inside_widget(widget: QWidget, point: QPointF) -> bool:
        return 0.0 <= point.x() <= float(widget.width()) and 0.0 <= point.y() <= float(
            widget.height()
        )

    @staticmethod
    def _points_are_close(a: QPointF, b: QPointF, *, tolerance: float = 2.0) -> bool:
        return abs(a.x() - b.x()) <= tolerance and abs(a.y() - b.y()) <= tolerance

    def _source_local_pos_from_event(self, event: Any, source: QWidget) -> QPointF:
        source_pos = event.position()
        event_local = QPointF(float(source_pos.x()), float(source_pos.y()))
        global_local: QPointF | None = None
        if hasattr(event, "globalPosition"):
            global_pos = event.globalPosition()
            global_point = global_pos.toPoint()
            mapped = source.mapFromGlobal(global_point)
            global_local = QPointF(
                float(mapped.x()) + (global_pos.x() - float(global_point.x())),
                float(mapped.y()) + (global_pos.y() - float(global_point.y())),
            )

        # Native child widgets on Windows can report position() in an ancestor
        # coordinate space while globalPosition() still maps back to the real
        # source-local point.  Prefer an ancestor-adjusted point only when it
        # agrees with globalPosition(); synthetic tests and old Qt paths with a
        # bogus global position keep using the direct source-local event point.
        if global_local is not None:
            ancestor = source.parentWidget()
            while ancestor is not None:
                origin = source.mapTo(ancestor, QPoint(0, 0))
                if origin.x() or origin.y():
                    adjusted = QPointF(
                        event_local.x() - float(origin.x()),
                        event_local.y() - float(origin.y()),
                    )
                    if self._point_inside_widget(source, adjusted) and self._points_are_close(
                        adjusted,
                        global_local,
                    ):
                        return adjusted
                if ancestor is self.window():
                    break
                ancestor = ancestor.parentWidget()

        if self._point_inside_widget(source, event_local):
            return event_local
        if global_local is not None and self._point_inside_widget(source, global_local):
            return global_local
        return event_local

    def _legacy_local_pos_from_event(self, event: Any, source: QWidget) -> QPointF:
        source_pos = self._source_local_pos_from_event(event, source)
        source_point = source_pos.toPoint()
        local = self.legacy_canvas.mapFromGlobal(source.mapToGlobal(source_point))
        return QPointF(
            float(local.x()) + (source_pos.x() - float(source_point.x())),
            float(local.y()) + (source_pos.y() - float(source_point.y())),
        )

    def _forward_mouse_event_to_legacy(
        self,
        event: QMouseEvent,
        handler_name: str,
        *,
        source: QWidget,
    ) -> None:
        if self._consume_input_if_canvas_readiness_gate(event):
            return
        local_pos = self._legacy_local_pos_from_event(event, source)
        forwarded = QMouseEvent(
            event.type(),
            local_pos,
            event.globalPosition(),
            event.button(),
            event.buttons(),
            event.modifiers(),
        )
        getattr(self.legacy_canvas, handler_name)(forwarded)
        event.setAccepted(forwarded.isAccepted())

    def _forward_wheel_event_to_legacy(self, event: QWheelEvent, *, source: QWidget) -> None:
        if self._consume_input_if_canvas_readiness_gate(event):
            return
        local_pos = self._legacy_local_pos_from_event(event, source)
        forwarded = QWheelEvent(
            local_pos,
            event.globalPosition(),
            event.pixelDelta(),
            event.angleDelta(),
            event.buttons(),
            event.modifiers(),
            event.phase(),
            event.inverted(),
            event.source(),
            event.pointingDevice(),
        )
        self.legacy_canvas.wheelEvent(forwarded)
        event.setAccepted(forwarded.isAccepted())

    def _forward_tablet_event_to_legacy(self, event: QTabletEvent, *, source: QWidget) -> None:
        if self._consume_input_if_canvas_readiness_gate(event):
            return
        if not hasattr(event, "globalPosition"):
            self.legacy_canvas.tabletEvent(event)
            return
        local_pos = self._legacy_local_pos_from_event(event, source)
        forwarded = QTabletEvent(
            event.type(),
            event.pointingDevice(),
            local_pos,
            event.globalPosition(),
            event.pressure(),
            event.xTilt(),
            event.yTilt(),
            event.tangentialPressure(),
            event.rotation(),
            event.z(),
            event.modifiers(),
            event.button(),
            event.buttons(),
        )
        self.legacy_canvas.tabletEvent(forwarded)
        event.setAccepted(forwarded.isAccepted())

    def _consume_input_if_canvas_readiness_gate(self, event: Any) -> bool:
        if not self._canvas_readiness_gate_active:
            return False
        accept = getattr(event, "accept", None)
        if callable(accept):
            accept()
        return True

    def _present_native_surface(  # noqa: PLR0911
        self,
        *,
        force_full_recompose: bool = False,
    ) -> str | None:
        canvas = self.legacy_canvas._get_canvas_service()
        if canvas is None:
            return None
        try:
            if (
                not self._native_visual_probe_passed
                and not self._native_surface_ready_for_visual_probe()
            ):
                return _NATIVE_VISUAL_PROBE_NOT_READY
            presenter = self._ensure_native_presenter()
            if presenter is None:
                return self._native_disabled_reason or "wgpu native surface unavailable"
            if not self._native_visual_probe_passed:
                probe_reason = self._run_native_visual_probe(presenter)
                if probe_reason is not None:
                    return probe_reason
            scene = self._build_native_render_scene(canvas)
            self._native_overlay_flat_layers = tuple(scene.flat_layers)
            invalidation = self.legacy_canvas._consume_render_invalidation(canvas)
            if force_full_recompose:
                invalidation = RenderInvalidation(full_recompose=True)
            companion = self._spread_companion_frame()
            presented = presenter.present_frame(
                scene,
                invalidation,
                device_pixel_ratio=self.devicePixelRatioF(),
                # Passed only when set so duck-typed test doubles keep working.
                **({"companion": companion} if companion is not None else {}),
            )
            if presented:
                self._native_scene_presented_after_probe = True
        except WgpuDeviceLostError as exc:
            self._native_device_lost_count += 1
            return f"wgpu native device lost: {exc}"
        except (WgpuBackendError, WgpuUnavailableError) as exc:
            return f"wgpu native render failed: {exc}"
        except Exception as exc:
            logger.debug("wgpu native render exception", exc_info=True)
            return f"unexpected wgpu native render failure: {exc}"
        for warning in presenter.last_warnings:
            logger.debug("wgpu native presenter: %s", warning)
        return None

    def fit_spread_to_window(self) -> bool:
        """Fit the whole spread, not just the page being edited (CT-06 §6.3).

        Returns ``False`` when there is no spread to fit, so the caller can
        fall back to the single-page fit.
        """

        state = self._spread_state()
        if state is None:
            return False
        left, top, width, height = state.spread_canvas_rect()
        if width <= 0 or height <= 0:
            return False
        self.legacy_canvas.viewport.fit_to_rect(left, top, width, height)
        self._request_native_present(force_full_recompose=True)
        self.update()
        return True

    def _spread_state(self) -> Any | None:
        controller = self._spread_controller
        if controller is None:
            return None
        try:
            return controller.state()
        except Exception:
            logger.debug("spread state unavailable", exc_info=True)
            return None

    def spread_blocks_canvas_point(self, x: float, y: float) -> bool:
        """Whether a canvas point lands on the spread's facing slot.

        The input handler asks before starting a stroke: the facing page is
        a read-only preview, and an empty slot has no page behind it at
        all, so neither may reach a tool (spec §6.3).
        """
        state = self._spread_state()
        if state is None:
            return False
        rect = state.companion_canvas_rect()
        if rect is None:
            return False
        left, top, width, height = rect
        return left <= x < left + width and top <= y < top + height

    def _paint_spread_frames(self, painter: QPainter) -> None:
        """Outline both slots so the edit target is unambiguous."""

        state = self._spread_state()
        if state is None:
            return
        try:
            transform = self.legacy_canvas.viewport.get_transform()
            scale = max(abs(float(transform.m11())), abs(float(transform.m22())), 1e-6)
            draw_spread_frames(
                painter,
                active_rect=state.active_canvas_rect(),
                companion_rect=state.companion_canvas_rect(),
                view_scale=scale,
            )
            draw_binding_danger(painter, state, getattr(self.legacy_canvas, "_host_api", None))
        except Exception:
            logger.debug("spread frame overlay failed", exc_info=True)

    def _spread_companion_frame(self) -> Any | None:
        """The spread's facing page, or ``None`` in single-page mode."""

        controller = self._spread_controller
        if controller is None:
            return None
        try:
            return controller.companion_frame()
        except Exception:
            logger.debug("spread companion frame unavailable", exc_info=True)
            return None

    def _build_native_render_scene(self, canvas: Any) -> Any:
        overlay_enabled = bool(getattr(self.legacy_canvas, "_overlay_enabled", False))
        eligible_ids: set[str] = set()
        if overlay_enabled:
            eligibility_scene = build_render_scene_snapshot(
                canvas,
                rasterize_vector_layers=False,
                include_selection_preview=False,
            )
            sync = getattr(
                self.legacy_canvas._overlay_manager,
                "sync_eligibility_transitions",
                None,
            )
            if callable(sync):
                eligible_ids = set(sync(canvas, flat=list(eligibility_scene.flat_layers)))

        scene = build_render_scene_snapshot(
            canvas,
            rasterize_vector_layers=True,
            include_selection_preview=False,
            skip_rasterize_layer_ids=frozenset(eligible_ids),
            vector_raster_scale=self._native_vector_raster_scale(canvas),
            include_frame_preview=True,
        )
        return scene

    def _native_vector_raster_scale(self, canvas: Any) -> int:
        """Raster prescale for text/non-direct vector layers (C2).

        Active only in view-resolution compositing mode, where the composite
        runs at viewport pixels and canvas-resolution rasters would blur.
        The rasterizer clamps per layer against its tight content region.
        """

        del canvas
        if not view_resolution_compositing_enabled():
            return 1
        try:
            transform = self.legacy_canvas.viewport.get_transform()
            dpr = max(1.0, float(self.devicePixelRatioF()))
            zoom = max(
                math.hypot(float(transform.m11()), float(transform.m12())),
                math.hypot(float(transform.m21()), float(transform.m22())),
            )
            return vector_raster_scale_bucket(zoom * dpr)
        except Exception:
            logger.debug("vector raster scale unavailable; using canvas resolution", exc_info=True)
            return 1

    def _run_native_visual_probe(self, presenter: WgpuNativeCanvasPresenter) -> str | None:
        return self._native_lifecycle().run_visual_probe(presenter)

    def _hide_overlapping_top_level_chrome_for_probe(self) -> list[QWidget]:
        return self._native_lifecycle().hide_overlapping_top_level_chrome_for_probe()

    def _restore_top_level_chrome_after_probe(self, widgets: list[QWidget]) -> None:
        self._native_lifecycle().restore_top_level_chrome_after_probe(widgets)

    @staticmethod
    def _widget_belongs_to_root(widget: QWidget, root: QWidget) -> bool:
        return NativeVisualProbeCoordinator.widget_belongs_to_root(widget, root)

    def _run_native_visual_probe_without_overlay(
        self,
        presenter: WgpuNativeCanvasPresenter,
    ) -> str | None:
        return self._native_lifecycle().run_visual_probe_without_overlay(presenter)

    def _capture_native_surface_image(self) -> QImage | None:
        return self._native_lifecycle().capture_native_surface_image()

    def _capture_native_surface_window_image(self) -> QImage | None:
        return self._native_lifecycle().capture_native_surface_window_image()

    def _record_native_visual_probe(
        self,
        presenter: WgpuNativeCanvasPresenter,
        *,
        status: str,
        reason: str | None,
        capture_supported: bool,
    ) -> None:
        self._native_lifecycle().record_visual_probe(
            presenter,
            status=status,
            reason=reason,
            capture_supported=capture_supported,
        )

    @staticmethod
    def _image_matches_native_smoke_frame(image: QImage) -> bool:
        return NativeVisualProbeCoordinator.image_matches_native_smoke_frame(
            image,
            NATIVE_SMOKE_CLEAR_RGBA,
        )

    @staticmethod
    def _linear_to_srgb(value: float) -> float:
        return NativeVisualProbeCoordinator.linear_to_srgb(value)

    def _native_lifecycle(self) -> CanvasViewportNativeLifecycleCoordinator:
        coordinator = getattr(self, "_native_lifecycle_coordinator", None)
        if not isinstance(coordinator, CanvasViewportNativeLifecycleCoordinator):
            coordinator = CanvasViewportNativeLifecycleCoordinator(
                self,
                presenter_factory=self._create_native_presenter_for_lifecycle,
                timer_single_shot=lambda delay_ms, callback: QTimer.singleShot(
                    delay_ms,
                    callback,
                ),
                unavailable_error_type=WgpuUnavailableError,
                backend_error_type=WgpuBackendError,
                not_ready_reason=_NATIVE_VISUAL_PROBE_NOT_READY,
                transient_mismatch_reason=_NATIVE_VISUAL_PROBE_TRANSIENT_MISMATCH,
                deferred_retries=_NATIVE_VISUAL_PROBE_DEFERRED_RETRIES,
                capture_attempts=_NATIVE_VISUAL_PROBE_CAPTURE_ATTEMPTS,
                capture_settle_seconds=_NATIVE_VISUAL_PROBE_CAPTURE_SETTLE_SECONDS,
                smoke_clear_rgba=NATIVE_SMOKE_CLEAR_RGBA,
            )
            self._native_lifecycle_coordinator = coordinator
        return coordinator

    def _create_native_presenter_for_lifecycle(self) -> WgpuNativeCanvasPresenter:
        presenter = WgpuNativeCanvasPresenter(
            window_id_provider=lambda: int(self._native_surface.winId()),
            surface_size_provider=self._native_physical_size,
            viewport_provider=lambda: self.viewport,
        )
        presenter._native_cmyk_repaint = self._request_native_cmyk_present
        if self._soft_proof_lut is not None:
            # Device loss must not silently drop an active proof.
            presenter.set_soft_proof_lut(self._soft_proof_lut)
        return presenter

    def _request_native_cmyk_present(self):
        self._request_native_present(force_full_recompose=True)
        self.update()
        self._overlay_surface.update()

    def _paint_native_cmyk_pending(self, painter):
        from .native_cmyk_pending_overlay import NativeCmykPendingOverlay  # noqa: PLC0415

        NativeCmykPendingOverlay.paint(self, painter)

    def _ensure_native_presenter(self) -> WgpuNativeCanvasPresenter | None:
        return self._native_lifecycle().ensure_presenter()

    def _activate_legacy_fallback(self, reason: str) -> None:
        if self._native_disabled_reason is None:
            self._native_fallback_count += 1
        self._native_disabled_reason = reason
        logger.warning("Disabling wgpu native surface; falling back: %s", reason)
        self._release_native_presenter()
        self.legacy_canvas._wgpu_bitmap_requested = True
        self._native_active = False
        self._apply_surface_visibility()
        canvas = self.legacy_canvas._get_canvas_service()
        invalidate = getattr(canvas, "invalidate_gpu_caches", None)
        if callable(invalidate):
            invalidate()
        self.legacy_canvas.update()

    def _release_native_presenter(self) -> None:
        self._native_lifecycle().release_presenter()

    def _schedule_native_visual_probe(self) -> None:
        self._native_lifecycle().schedule_visual_probe()

    def _run_deferred_native_visual_probe(self) -> None:  # noqa: PLR0911
        self._native_lifecycle().run_deferred_visual_probe()

    def _native_surface_ready_for_visual_probe(self) -> bool:
        if not self.isVisible() or not self._native_surface.isVisible():
            return False
        if self._native_surface.width() < 16 or self._native_surface.height() < 16:
            return False
        handle = self._native_surface.windowHandle()
        if handle is None:
            return False
        is_exposed = getattr(handle, "isExposed", None)
        return not (callable(is_exposed) and not bool(is_exposed()))

    def _native_physical_size(self) -> tuple[int, int]:
        dpr = max(1.0, float(self.devicePixelRatioF()))
        return (
            max(1, round(self._native_surface.width() * dpr)),
            max(1, round(self._native_surface.height() * dpr)),
        )

    def _sync_child_geometry(self) -> None:
        rect = QRect(0, 0, max(1, self.width()), max(1, self.height()))
        self.legacy_canvas.setGeometry(rect)
        self._native_surface.setGeometry(rect)
        self._sync_overlay_root_event_filter()
        self._overlay_surface.setGeometry(self._overlay_surface_geometry())
        # Keep transient input widgets (notably the text editor) above the
        # drawing plane when layout/window geometry is synchronized.
        self._native_surface.lower()
        if self._overlay_surface.isVisible():
            self._overlay_surface.raise_()
            self._raise_overlapping_top_level_chrome()

    def _overlay_surface_geometry(self) -> QRect:
        try:
            top_left = self.mapToGlobal(QPoint(0, 0))
        except RuntimeError:
            top_left = QPoint(0, 0)
        return QRect(
            int(top_left.x()),
            int(top_left.y()),
            max(1, int(self.width())),
            max(1, int(self.height())),
        )

    def _sync_overlay_root_event_filter(self) -> None:
        root = self.window()
        if root is self._overlay_event_root:
            return
        self._remove_overlay_root_event_filter()
        if root is None or root is self:
            return
        root.installEventFilter(self)
        self._overlay_event_root = root

    def _remove_overlay_root_event_filter(self) -> None:
        root = self._overlay_event_root
        if root is None:
            return
        if _is_qt_object_valid(root):
            root.removeEventFilter(self)
        self._overlay_event_root = None

    def _native_overlay_viewport_offset(self) -> QPoint:
        # CanvasWidget's viewport transform maps into legacy_canvas-local
        # coordinates.  The overlay painter uses _overlay_surface-local
        # coordinates, so compensate only for the actual global origin delta
        # between those two drawing planes.  A top-level transparent overlay
        # cannot be mapped through the host-local child hierarchy reliably.
        try:
            legacy_origin = self.legacy_canvas.mapToGlobal(QPoint(0, 0))
            overlay_origin = self._overlay_surface.mapToGlobal(QPoint(0, 0))
        except RuntimeError:
            return QPoint(0, 0)
        return QPoint(
            overlay_origin.x() - legacy_origin.x(),
            overlay_origin.y() - legacy_origin.y(),
        )

    @staticmethod
    def _native_overlay_canvas_transform(
        transform: QTransform, viewport_offset: QPoint
    ) -> QTransform:
        if viewport_offset.isNull():
            return transform
        correction = QTransform()
        correction.translate(-float(viewport_offset.x()), -float(viewport_offset.y()))
        return transform * correction

    def _apply_surface_visibility(self) -> None:
        if self.native_surface_active:
            self._sync_child_geometry()
            self.legacy_canvas.hide()
            self._native_surface.show()
            self._native_surface.lower()
            self._overlay_surface.show()
            self._overlay_surface.raise_()
            self._raise_overlapping_top_level_chrome()
            self._request_native_present(force_full_recompose=True)
        else:
            self._native_surface.hide()
            self._overlay_surface.hide()
            self.legacy_canvas.show()

    def _relay_legacy_signals(self) -> None:
        self.legacy_canvas.stroke_started.connect(self.stroke_started.emit)
        self.legacy_canvas.stroke_updated.connect(self.stroke_updated.emit)
        self.legacy_canvas.stroke_ended.connect(self.stroke_ended.emit)
        self.legacy_canvas.pointer_hovered.connect(self.pointer_hovered.emit)
        self.legacy_canvas.viewport_changed.connect(self._on_legacy_viewport_changed)
        self.legacy_canvas.canvas_resized.connect(self.canvas_resized.emit)
        self.legacy_canvas.context_menu_requested.connect(self.context_menu_requested.emit)

    def _on_legacy_viewport_changed(self, state: dict[str, Any]) -> None:
        self.viewport_changed.emit(state)
        self._request_native_present()
        self.update()

    def _on_composited_changed(self, pixmap: QPixmap) -> None:
        del pixmap
        self._request_native_present()
        self.update()

    def _raise_overlapping_top_level_chrome(self) -> None:
        """Keep floating tool UI above the transparent native overlay window."""

        root = self.window()
        if root is None:
            return
        try:
            overlay_rect = self._overlay_surface.frameGeometry()
        except RuntimeError:
            return
        for widget in QApplication.topLevelWidgets():
            if widget is root or widget is self or widget is self._overlay_surface:
                continue
            if not widget.isVisible() or not widget.isWindow():
                continue
            if not bool(widget.windowFlags() & Qt.WindowType.Tool):
                continue
            if not self._widget_belongs_to_root(widget, root):
                continue
            if not widget.frameGeometry().intersects(overlay_rect):
                continue
            widget.raise_()

    def _request_native_present(self, *, force_full_recompose: bool = False) -> None:
        if not self.native_surface_active:
            return
        if self._native_visual_probe_running:
            if force_full_recompose:
                self._native_force_next_present = True
            return
        if not self.isVisible() or not self._native_surface.isVisible():
            if force_full_recompose:
                self._native_force_next_present = True
            return
        if force_full_recompose:
            self._native_force_next_present = True
        if not self._native_visual_probe_passed:
            self._schedule_native_visual_probe()
            return
        if self._native_present_pending:
            return
        self._native_present_pending = True
        QTimer.singleShot(0, self._run_scheduled_native_present)

    def _run_scheduled_native_present(self) -> None:
        self._native_present_pending = False
        if (
            not _is_qt_object_valid(self)
            or not _is_qt_object_valid(self._native_surface)
            or not _is_qt_object_valid(self._overlay_surface)
            or not self.native_surface_active
        ):
            return
        if not self.isVisible() or not self._native_surface.isVisible():
            return
        if self._native_visual_probe_running:
            return
        force_full_recompose = self._native_force_next_present
        reason = self._present_native_surface(force_full_recompose=force_full_recompose)
        if reason is None:
            self._native_force_next_present = False
            if self._native_flush_after_present:
                self._native_flush_after_present = False
                self._flush_native_surface_window()
            self._repaint_native_overlay_surface()
            return
        if reason == _NATIVE_VISUAL_PROBE_NOT_READY:
            self._schedule_native_visual_probe()
            return
        self._activate_legacy_fallback(reason)

    def _flush_native_surface_window(self) -> None:
        """Force Windows to repaint the native child surface after overlay UI moves."""

        if os.name != "nt":
            return
        try:
            hwnd = int(self._native_surface.winId())
        except (RuntimeError, TypeError, ValueError):
            return
        if hwnd <= 0:
            return
        try:
            user32 = ctypes.windll.user32
            # The first present updates wgpu's surface content.  RedrawWindow
            # then makes Win32/DWM consume the newly presented child HWND region
            # after a sibling Qt toolbar vacated that rectangle.
            rdw_invalidate = 0x0001
            rdw_nochildren = 0x0040
            rdw_updatenow = 0x0100
            user32.RedrawWindow(
                ctypes.c_void_p(hwnd),
                None,
                None,
                rdw_invalidate | rdw_nochildren | rdw_updatenow,
            )
        except Exception:
            logger.debug("Failed to flush native surface HWND", exc_info=True)

    def _repaint_native_overlay_surface(self) -> None:
        """Synchronize the top-level overlay immediately after a native present."""

        if (
            not self.native_surface_active
            or not _is_qt_object_valid(self._overlay_surface)
            or not self._overlay_surface.isVisible()
        ):
            return
        # The overlay is a separate top-level tool window.  A queued update can
        # land one Qt event after the native child surface present, making SSO
        # appear to trail during viewport pan/zoom.
        self._overlay_surface.repaint()

    @staticmethod
    def _overlay_timer_start() -> float:
        return time.perf_counter()

    def _record_overlay_paint_ms(self, start: float) -> None:
        if self._native_presenter is None:
            return

        self._native_presenter.diagnostics.overlay_paint_ms = (time.perf_counter() - start) * 1000.0

    def _paint_canvas_readiness_gate(self, painter: QPainter) -> None:
        if not self._canvas_readiness_gate_active:
            return

        rect = self._overlay_surface.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        painter.save()
        try:
            painter.resetTransform()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.fillRect(rect, QColor(10, 12, 16, 132))

            panel_width = min(max(240, rect.width() // 3), 380)
            panel_height = 82
            panel = QRect(
                rect.x() + max(0, (rect.width() - panel_width) // 2),
                rect.y() + max(0, (rect.height() - panel_height) // 2),
                panel_width,
                panel_height,
            )
            painter.setPen(QPen(QColor(255, 255, 255, 72), 1))
            painter.setBrush(QColor(28, 31, 38, 230))
            painter.drawRoundedRect(panel, 6, 6)

            font = painter.font()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(238, 242, 248, 255))
            message = self._canvas_readiness_gate_message or "Preparing canvas..."
            painter.drawText(
                panel.adjusted(18, 14, -18, -30),
                Qt.AlignmentFlag.AlignCenter,
                message,
            )

            bar = QRect(panel.left() + 26, panel.bottom() - 24, panel.width() - 52, 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 42))
            painter.drawRoundedRect(bar, 2, 2)

            phase = time.perf_counter() % 1.0
            segment_width = max(44, bar.width() // 3)
            segment_x = bar.left() + int((bar.width() + segment_width) * phase) - segment_width
            segment = QRect(segment_x, bar.top(), segment_width, bar.height()).intersected(bar)
            if not segment.isEmpty():
                painter.setBrush(QColor(92, 170, 255, 230))
                painter.drawRoundedRect(segment, 2, 2)
        finally:
            painter.restore()
