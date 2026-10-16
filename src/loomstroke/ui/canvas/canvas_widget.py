"""Canvas widget for drawing area implementation."""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QImageReader,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTabletEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.host_api import HostAPI, StrokePoint

from .canvas_diagnostics import build_canvas_widget_render_diagnostics
from .canvas_presenter import CanvasPresenterDiagnostics
from .canvas_wgpu_bitmap_coordinator import CanvasWidgetWgpuBitmapCoordinator
from .guide_controller import GuideController
from .input_handler import InputHandler
from .projection_state import ProjectionStateTracker
from .render_scene import (
    RenderSceneSnapshot,
    build_render_scene_snapshot,
)
from .screen_space_overlay import ScreenSpaceOverlayManager
from .three_d_interaction import Canvas3DLayerInteractionController
from .vector_selection_overlay import draw_vector_selection_overlay
from .viewport import Viewport
from .wgpu_backend import WgpuBackendError, WgpuDeviceLostError, WgpuUnavailableError
from .wgpu_canvas_presenter import WgpuBitmapPresenter

logger = logging.getLogger(__name__)
_IMAGE_FILE_EXTENSIONS = {
    bytes(fmt.data()).decode("ascii", errors="ignore").lower() if hasattr(fmt, "data") else str(fmt)
    for fmt in QImageReader.supportedImageFormats()
}
_FORCE_CPU_CANVAS = os.environ.get("LOOMSTROKE_FORCE_CPU_CANVAS", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_WGPU_BITMAP_MAX_READBACK_MS = 100.0
_WGPU_BITMAP_SLOW_FRAME_LIMIT = 2
_WGPU_BITMAP_AUTO_DISABLE_ON_SLOW_READBACK = os.environ.get(
    "LOOMSTROKE_WGPU_BITMAP_AUTO_DISABLE_ON_SLOW_READBACK",
    "",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_GUIDE_MOVE_TOOL_IDS = frozenset({"basic_move", "loomstroke.basic_move"})


def _select_render_backend(value: str | None) -> str:
    """Resolve the render backend override.

    Windows routes default wgpu aliases through the native surface path.
    Platforms that have not passed the live-surface oracle keep the bitmap
    compatibility path and do not route new work into the GL backend.
    """

    token = (value or "").strip().lower().replace("-", "_")
    if token in {"opengl", "open_gl", "gl", "legacy_gl"}:
        logger.warning(
            "LOOMSTROKE_RENDER_BACKEND=%r requested the retired OpenGL backend; using wgpu",
            value,
        )
        return "wgpu_native" if sys.platform == "win32" else "wgpu_bitmap"
    if token in {"wgpu_native", "webgpu_native", "native"}:
        return "wgpu_native"
    if token in {"wgpu_bitmap", "webgpu_bitmap", "bitmap"}:
        return "wgpu_bitmap"
    if token in {"wgpu", "webgpu", "auto", "default", ""}:
        return "wgpu_native" if sys.platform == "win32" else "wgpu_bitmap"
    logger.warning(
        "Unknown LOOMSTROKE_RENDER_BACKEND=%r; using wgpu bitmap with fallback",
        value,
    )
    return "wgpu_bitmap"


_RENDER_BACKEND_SELECTION = _select_render_backend(os.environ.get("LOOMSTROKE_RENDER_BACKEND"))
_WGPU_NATIVE_BACKEND_REQUESTED = _RENDER_BACKEND_SELECTION == "wgpu_native"
_WGPU_BITMAP_BACKEND_REQUESTED = _RENDER_BACKEND_SELECTION == "wgpu_bitmap"
_CanvasWidgetBase = QWidget


@dataclass
class CanvasState:
    """Canvas state management."""

    width: int = 1920
    height: int = 1080
    background_color: QColor = field(default_factory=lambda: QColor(255, 255, 255))
    grid_visible: bool = False
    grid_size: int = 32
    antialiasing: bool = True

    # Performance
    use_opengl: bool = False  # Legacy settings key; ignored by the wgpu host.
    buffer_size: int = 100  # Stroke point buffer


class CanvasWidget(_CanvasWidgetBase):
    """Main canvas widget for drawing operations.

    Handles rendering, input events, and coordinate transformation.
    Uses wgpu for accelerated layer compositing when available.
    """

    # Signals for tool interaction
    stroke_started = Signal(object)  # StrokePoint
    stroke_updated = Signal(list)  # list[StrokePoint]
    stroke_ended = Signal(object)  # StrokePoint
    pointer_hovered = Signal(object)  # StrokePoint

    # Canvas state signals
    viewport_changed = Signal(dict)  # Viewport state
    canvas_resized = Signal(int, int)  # width, height
    context_menu_requested = Signal(object)  # payload dict

    def __init__(self, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        """Initialize canvas widget."""
        super().__init__(parent)

        self.state = CanvasState()
        self.viewport = Viewport()
        self.input_handler = InputHandler(self)
        self._three_d_interaction = Canvas3DLayerInteractionController(self)

        # Drawing buffers
        self._buffers_initialized = False
        self._overlay_buffer: QPixmap | None = None
        self._temp_buffer: QPixmap | None = None
        self._show_stroke_preview = False

        # Tool preview overlay (e.g. gradient drag preview)
        self._tool_preview_image: QImage | None = None
        self._tool_preview_comp_mode: QPainter.CompositionMode = (
            QPainter.CompositionMode.CompositionMode_SourceOver
        )
        self._tool_preview_opacity: float = 1.0
        self._canvas_agent_overlay_fn: Any = None  # Callable[[QPainter], None] | None
        self._tool_overlay_fn: Any = None  # Callable[[QPainter], None] | None

        # Stroke accumulation
        self._current_stroke: list[StrokePoint] = []
        self._pending_stroke_points: list[StrokePoint] = []
        self._is_drawing = False
        self._pending_canvas_update = False
        self._stroke_move_emitted = False

        # Guide/ruler support
        self._guide_controller: GuideController | None = None

        # Context toolbar manager (set by MainWindow)
        self._context_toolbar_manager: Any = None

        # Performance
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._deferred_update)
        self._update_timer.setInterval(16)  # ~60fps
        self._dirty_region = QRectF()

        # Host API reference (will be set later)
        self._host_api: HostAPI | None = None

        # Marching-ants animation
        self._marching_ants_offset: float = 0.0
        self._marching_ants_timer = QTimer()
        self._marching_ants_timer.timeout.connect(self._tick_marching_ants)
        self._marching_ants_timer.setInterval(80)  # ~12fps for animation
        self._ants_path_cache: QPainterPath | None = None
        self._ants_path_mask_key: int | None = None  # QImage.cacheKey()

        self._gl_compositor = SimpleNamespace(
            _initialized=False,
            _layer_textures={},
            _effected_textures={},
            get_layer_texture=lambda _layer_id: None,
            clear_effected_texture=lambda _layer_id: None,
        )
        self._gl_effect_processor = SimpleNamespace(
            is_initialized=False,
            mark_cache_stale=lambda _layer_id: None,
        )
        self._gl_layer_compositor = SimpleNamespace(
            invalidate_without_gl=lambda: None,
            remove_mask_texture=lambda _layer_id: None,
        )
        self._render_backend = None
        self._canvas_presenter = SimpleNamespace(
            diagnostics=CanvasPresenterDiagnostics(
                backend_name="wgpu-native-controller",
                fallback_tier="wgpu_native",
            )
        )
        self._gl_transform_processor = SimpleNamespace(is_initialized=False)
        self._gl_compute_processor = SimpleNamespace()
        self._wgpu_bitmap_presenter: WgpuBitmapPresenter | None = None
        self._wgpu_bitmap_requested = _WGPU_BITMAP_BACKEND_REQUESTED
        self._wgpu_bitmap_disabled_reason: str | None = None
        self._wgpu_bitmap_fallback_count = 0
        self._wgpu_bitmap_device_lost_count = 0
        self._wgpu_bitmap_slow_readback_count = 0
        self._last_wgpu_bitmap_scene_signature: tuple[Any, ...] | None = None
        self._wgpu_bitmap_coordinator = CanvasWidgetWgpuBitmapCoordinator(
            self,
            presenter_factory=lambda: WgpuBitmapPresenter(),
            unavailable_error_type=WgpuUnavailableError,
            backend_error_type=WgpuBackendError,
            max_readback_ms=_WGPU_BITMAP_MAX_READBACK_MS,
            slow_frame_limit=_WGPU_BITMAP_SLOW_FRAME_LIMIT,
            auto_disable_on_slow_readback=_WGPU_BITMAP_AUTO_DISABLE_ON_SLOW_READBACK,
        )
        self._gl_context_manager = SimpleNamespace(
            cleanup_for_shutdown=lambda: None,
            cleanup_for_context_loss=lambda: None,
        )
        self._gl_initialized = False
        # Set to True after GL context loss; consumed on next initializeGL()
        # to trigger a full cache invalidation on the canvas service side.
        self._needs_full_resync = False
        self._layer_tex_versions: dict[str, int] = {}
        # Vector layer FBO cache: layer_id -> (fbo_id, tex_id, w, h)
        self._vector_fbos: dict[str, tuple[int, int, int, int]] = {}

        # Projection cache state (gpu_projection_tile_spec Phase 2-5)
        self._projection_tracker = ProjectionStateTracker()
        self._projection_valid = False
        self._below_projection_valid = False
        self._force_cpu_composite = _FORCE_CPU_CANVAS
        self._paint_perf_frame = 0
        if self._force_cpu_composite:
            logger.warning(
                "LOOMSTROKE_FORCE_CPU_CANVAS is enabled; using CPU canvas compositing",
            )
        if _PEN_LAG_DIAG:
            logger.warning("LOOMSTROKE_PEN_LAG_DIAG is enabled; sampling canvas paint timings")
        if self._wgpu_bitmap_requested:
            logger.info("Using wgpu bitmap presenter")
        elif _WGPU_NATIVE_BACKEND_REQUESTED:
            logger.info("Using plain QWidget canvas controller for wgpu native host")
        else:
            logger.info("Using wgpu bitmap presenter")

        # Screen-Space Vector Overlay (SSO Phase 1, spec: resolution_independent_vector/)
        # デフォルト無効 (Beta 期 opt-in 配信、仕様 §9.1)。
        # main_window 側で settings `performance/vector_overlay_enabled` を
        # 読み、``set_overlay_enabled`` で切り替える。
        self._overlay_manager = ScreenSpaceOverlayManager()
        self._overlay_enabled = False

        # Checkerboard tile for transparent background display
        self._checkerboard_tile: QPixmap | None = None

        self._setup_ui()
        self._connect_signals()

        # Ensure viewport has current size after initialization
        size = self.size()
        if size.width() > 0 and size.height() > 0:
            self.viewport.set_widget_size(size.width(), size.height())
            self._overlay_manager.set_widget_size(size.width(), size.height())

    # ------------------------------------------------------------------
    # Screen-Space Vector Overlay (SSO Phase 1) — public settings hooks
    # ------------------------------------------------------------------

    def set_overlay_enabled(self, enabled: bool) -> None:
        """Toggle Screen-Space Vector Overlay (SSO Phase 1).

        main_window が起動時および preferences 変更時に
        ``performance/vector_overlay_enabled`` の値を渡して呼ぶ想定。
        False → True 遷移時は次フレームから SSO 描画が有効化され、
        True → False 遷移時はキャッシュを破棄して CST パスに復帰する。
        """
        new_enabled = bool(enabled)
        if self._overlay_enabled == new_enabled:
            return
        self._overlay_enabled = new_enabled
        self._overlay_manager.set_enabled(new_enabled)
        self.update()

    def set_overlay_max_dim(self, max_dim: int) -> None:
        """SSO の effective_dpr cap 値を設定する (4K/8K 運用向け調整)。"""
        value = max(512, int(max_dim))
        if value == self._overlay_manager.MAX_DIM:
            return
        self._overlay_manager.MAX_DIM = value
        # 既存 entry の invariant_key は dpr 依存だが max_dim 変化でも再ラスタ必要
        self._overlay_manager._entries.clear()
        self._overlay_manager._preview_image = None
        self.update()

    def set_overlay_debug_timing(self, enabled: bool) -> None:
        """SSO PD-3 計測 (Q-3 実測) の ON/OFF (Phase 1b)。

        True で ``_render_layer_to_overlay`` / ``render_preview`` の所要時間を
        deque に記録し、100 サンプル毎に DEBUG ログで p50/p95 を出力する。
        """
        self._overlay_manager.set_debug_timing(bool(enabled))

    def _setup_ui(self) -> None:
        """Setup UI properties."""
        self.setMouseTracking(True)
        self.setTabletTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setAcceptDrops(True)

        # Set minimum size
        self.setMinimumSize(400, 300)

        # Enable tablet events
        self.setAttribute(Qt.WidgetAttribute.WA_TabletTracking)

        # Initialize with minimum size to avoid zero-sized viewport
        self.viewport.set_widget_size(400, 300)

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        # Input handler signals
        self.input_handler.pointer_down.connect(self._on_pointer_down)
        self.input_handler.pointer_move.connect(self._on_pointer_move)
        self.input_handler.pointer_up.connect(self._on_pointer_up)
        self.input_handler.pointer_hover.connect(self._on_pointer_hover)

        # Viewport signals
        self.viewport.viewport_changed.connect(self._on_viewport_changed)

    def async_initialize(self) -> None:
        """Async initialization."""
        logger.info("Canvas widget initialized")
        self._initialize_buffers()

    def close_document_resources(self) -> None:
        """Release closed-document resources while keeping the canvas reusable."""
        from .canvas_document_close import close_canvas_document_resources  # noqa: PLC0415

        close_canvas_document_resources(self)

    def async_cleanup(self) -> None:
        """Async cleanup."""
        logger.info("Canvas widget cleanup")
        self._update_timer.stop()
        self._release_wgpu_bitmap_presenter()
        self._gl_context_manager.cleanup_for_shutdown()
        self._overlay_manager.cleanup()

    def _initialize_buffers(self) -> None:
        """Initialize drawing buffers."""
        size = self.size()

        self._buffers_initialized = True

        # Overlay for UI elements
        self._overlay_buffer = QPixmap(size)
        self._overlay_buffer.fill(Qt.GlobalColor.transparent)

        # Temporary for current stroke
        self._temp_buffer = QPixmap(size)
        self._temp_buffer.fill(Qt.GlobalColor.transparent)

        logger.debug(f"Buffers initialized: {size.width()}x{size.height()}")

    # --- GPU / CPU rendering toggle ---

    def set_gpu_enabled(self, enabled: bool) -> None:
        """Deprecated no-op shim — GPU is the sole rendering path (Phase 1).

        The CPU compositing path has been removed from normal operation.
        Calls with ``enabled=False`` are ignored with a warning; the
        setting is retained only for backwards compatibility with older
        plugins and legacy user settings.
        """
        if not enabled:
            logger.warning(
                "set_gpu_enabled(False) ignored — CPU rendering path has been removed",
            )
        # Nothing else to do: GPU is already initialized via initializeGL().

    def is_gpu_enabled(self) -> bool:
        """Return whether GPU rendering is currently active."""
        return self._wgpu_bitmap_disabled_reason is None

    def get_gpu_renderer_name(self) -> str:
        """Return the active wgpu presenter name when available."""
        if self._wgpu_bitmap_presenter is not None:
            adapter = self._wgpu_bitmap_presenter.diagnostics.adapter_name
            if adapter:
                return adapter
        return "wgpu-native-controller" if _WGPU_NATIVE_BACKEND_REQUESTED else "wgpu-bitmap"

    def get_render_diagnostics(self) -> dict[str, Any]:
        """Return optional render backend diagnostics for support/debug views."""
        return build_canvas_widget_render_diagnostics(
            self,
            selected_backend=_RENDER_BACKEND_SELECTION,
            native_requested=_WGPU_NATIVE_BACKEND_REQUESTED,
        )

    # --- Retired GL compatibility shims ---

    def _has_qopengl_widget_base(self) -> bool:
        return False

    def makeCurrent(self) -> None:  # noqa: N802
        return None

    def doneCurrent(self) -> None:  # noqa: N802
        return None

    def context(self) -> None:
        return None

    def defaultFramebufferObject(self) -> int:  # noqa: N802
        return 0

    def initializeGL(self) -> None:  # noqa: PLR0912
        """Retired OpenGL lifecycle hook kept as a no-op for Qt compatibility."""
        return None

    def _show_gpu_init_error_and_exit(self) -> None:
        """Display a fatal GPU init error dialog and quit the application."""

        logger.error("wgpu initialization failed")

        title = self.tr("GPU initialization failed")
        message = self.tr(
            "LoomStroke requires a wgpu-compatible graphics backend "
            "(D3D12, Vulkan, or Metal).\n\n"
            "Failed to initialise the GPU compositor. Please update your "
            "graphics driver and try again."
        )

        try:
            QMessageBox.critical(self, title, message)
        except Exception:
            logger.exception("Failed to show GPU init error dialog")

        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def _on_gl_context_destroyed(self) -> None:
        """Retired GL lifecycle hook kept as a no-op compatibility shim."""
        if not self._gl_initialized:
            return
        logger.warning("retired GL context hook invoked; clearing transient resources")
        self._release_wgpu_bitmap_presenter()
        self._gl_context_manager.cleanup_for_context_loss()
        self._overlay_manager.cleanup()
        self._layer_tex_versions.clear()
        self._gl_layer_compositor.invalidate_without_gl()
        self._vector_fbos.clear()
        self._gl_initialized = False
        self._projection_valid = False
        self._below_projection_valid = False
        self._needs_full_resync = True

    def resizeGL(self, w: int, h: int) -> None:
        """Retired GL resize hook kept for compatibility."""
        del w, h

    def paintEvent(self, event: Any) -> None:
        if self._has_qopengl_widget_base():
            super().paintEvent(event)
            return
        del event
        canvas = self._get_canvas_service()
        if canvas is None:
            return
        if self._force_cpu_composite:
            self._paint_cpu()
            return
        if not self._paint_wgpu_bitmap(canvas):
            self._paint_cpu()

    def paintGL(self) -> None:
        """Retired GL paint hook; paint through wgpu bitmap or CPU fallback."""
        canvas = self._get_canvas_service()
        if canvas is None:
            return
        if self._force_cpu_composite:
            canvas.consume_gpu_full_recompose()
            canvas.consume_gpu_dirty_rect()
            canvas.consume_gpu_mask_dirty_layers()
            self._paint_cpu()
            return
        if self._paint_wgpu_bitmap(canvas):
            return
        self._paint_cpu()

    # --- GPU transform (public API for tools/plugins) ---

    def apply_selection_transform_gpu(
        self,
        payload: Any,
        transform: Any,
        *,
        premultiplied: bool,
        mask_mode: str,
        canvas_size: tuple[int, int] | None = None,
    ) -> tuple[Any, Any, tuple[int, int, int, int]] | None:
        """Retired GPU transform hook.

        Raster selection transforms now use the existing CPU fallback path.
        Display-time layer transforms are handled by ``WgpuTransformProcessor``.
        """
        _ = (payload, transform, premultiplied, mask_mode, canvas_size)
        return None

    def warp_qimage_gpu(
        self,
        src: Any,
        dst_w: int,
        dst_h: int,
        matrix: tuple[float, float, float, float, float, float],
        bilinear: bool = True,
    ) -> Any | None:
        """Retired GPU image-warp hook; callers fall back to CPU on ``None``."""
        _ = (src, dst_w, dst_h, matrix, bilinear)
        return None

    # --- GPU rendering path ---

    def _wgpu_bitmap(self) -> CanvasWidgetWgpuBitmapCoordinator:
        coordinator = getattr(self, "_wgpu_bitmap_coordinator", None)
        if not isinstance(coordinator, CanvasWidgetWgpuBitmapCoordinator):
            coordinator = CanvasWidgetWgpuBitmapCoordinator(
                self,
                presenter_factory=lambda: WgpuBitmapPresenter(),
                unavailable_error_type=WgpuUnavailableError,
                backend_error_type=WgpuBackendError,
                max_readback_ms=_WGPU_BITMAP_MAX_READBACK_MS,
                slow_frame_limit=_WGPU_BITMAP_SLOW_FRAME_LIMIT,
                auto_disable_on_slow_readback=_WGPU_BITMAP_AUTO_DISABLE_ON_SLOW_READBACK,
            )
            self._wgpu_bitmap_coordinator = coordinator
        return coordinator

    def _ensure_wgpu_bitmap_presenter(self) -> WgpuBitmapPresenter | None:
        return cast("WgpuBitmapPresenter | None", self._wgpu_bitmap().ensure_presenter())

    def render_native_cmyk_layers(self, layers):
        from .native_cmyk_scene_renderer import NativeCmykSceneRenderer  # noqa: PLC0415

        return NativeCmykSceneRenderer.from_canvas(self._get_canvas_service(), layers)

    def _disable_wgpu_bitmap(self, reason: str, canvas: Any | None = None) -> None:
        self._wgpu_bitmap().disable(reason, canvas)

    def _release_wgpu_bitmap_presenter(self) -> None:
        self._wgpu_bitmap().release_presenter()

    def _render_or_reuse_wgpu_bitmap_frame(
        self,
        presenter: WgpuBitmapPresenter,
        scene: RenderSceneSnapshot,
        invalidation: RenderInvalidation,
    ) -> tuple[QPixmap | None, bool]:
        return self._wgpu_bitmap().render_or_reuse_frame(presenter, scene, invalidation)

    @staticmethod
    def _wgpu_bitmap_scene_signature(scene: RenderSceneSnapshot) -> tuple[Any, ...]:
        """Return a cheap key for presenter-level frame reuse."""

        return CanvasWidgetWgpuBitmapCoordinator.scene_signature(scene)

    def _paint_wgpu_bitmap(  # noqa: PLR0911, PLR0912
        self, canvas: Any, *, paint_widget: bool = True
    ) -> bool:
        """Render the frame, painting the QWidget only inside its paint event."""
        presenter = self._ensure_wgpu_bitmap_presenter()
        if presenter is None:
            return False
        perf_start = time.perf_counter() if _PEN_LAG_DIAG else 0.0
        try:
            scene = build_render_scene_snapshot(
                canvas,
                rasterize_vector_layers=True,
                include_selection_preview=False,
                include_frame_preview=True,
            )
            invalidation = self._consume_render_invalidation(canvas)
            pixmap, frame_rendered = self._render_or_reuse_wgpu_bitmap_frame(
                presenter,
                scene,
                invalidation,
            )
        except WgpuDeviceLostError as exc:
            self._wgpu_bitmap_device_lost_count += 1
            self._disable_wgpu_bitmap(f"wgpu device lost: {exc}", canvas)
            return False
        except WgpuBackendError as exc:
            self._disable_wgpu_bitmap(f"wgpu render failed: {exc}", canvas)
            return False
        except Exception as exc:
            self._disable_wgpu_bitmap(f"unexpected wgpu render failure: {exc}", canvas)
            logger.debug("wgpu bitmap render exception", exc_info=True)
            return False
        if pixmap is None or pixmap.isNull():
            self._disable_wgpu_bitmap("wgpu returned an empty frame", canvas)
            return False

        dirty_rect = invalidation.dirty_rect_copy()
        if dirty_rect is not None:
            presenter.diagnostics.dirty_rect_size = (
                dirty_rect.width(),
                dirty_rect.height(),
            )
        presenter.diagnostics.fallback_to_gl_count = 0
        presenter.diagnostics.device_lost_count = self._wgpu_bitmap_device_lost_count
        if frame_rendered:
            for warning in presenter.last_warnings:
                logger.debug("wgpu bitmap presenter: %s", warning)
            if self._maybe_disable_wgpu_bitmap_for_slow_readback(presenter, canvas):
                return False
        else:
            self._wgpu_bitmap_slow_readback_count = 0

        if not paint_widget:
            return True

        painter = QPainter(self)
        try:
            painter.setTransform(self.viewport.get_transform())
            painter.drawPixmap(
                QRectF(0.0, 0.0, float(scene.width), float(scene.height)),
                pixmap,
                QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height())),
            )
            painter.resetTransform()

            if scene.background.alpha() < 255:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOver)
                painter.setTransform(self.viewport.get_transform())
                self._draw_checkerboard(painter, scene.width, scene.height)
                painter.resetTransform()
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            overlay_start = time.perf_counter()
            self._draw_selection_preview(painter, canvas)
            painter.setTransform(self.viewport.get_transform())
            self._draw_mask_overlays(painter, canvas)
            painter.resetTransform()

            flat_layers = list(scene.flat_layers)
            if self._overlay_enabled:
                dpr = self.devicePixelRatioF()
                self._overlay_manager.render_eligible_layers(
                    painter,
                    canvas,
                    self.viewport,
                    dpr,
                    flat=flat_layers,
                )
                self._overlay_manager.render_preview(
                    painter,
                    canvas,
                    self.viewport,
                    dpr,
                    flat=flat_layers,
                )

            if self.state.antialiasing:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

            painter.setTransform(self.viewport.get_transform())
            self._draw_overlays(painter)
            presenter.diagnostics.overlay_paint_ms = (time.perf_counter() - overlay_start) * 1000.0
        finally:
            painter.end()
        self._log_paint_perf("wgpu-bitmap", perf_start)
        return True

    def _maybe_disable_wgpu_bitmap_for_slow_readback(
        self,
        presenter: WgpuBitmapPresenter,
        canvas: Any,
    ) -> bool:
        return self._wgpu_bitmap().maybe_disable_for_slow_readback(presenter, canvas)

    def _paint_gpu(self, canvas: Any) -> None:
        """Compatibility paint entrypoint routed through wgpu bitmap."""
        if not self._paint_wgpu_bitmap(canvas):
            self._paint_cpu()

    # ------------------------------------------------------------------
    # Projection cache helpers (gpu_projection_tile_spec §4.13)
    # ------------------------------------------------------------------

    def _can_use_projection_cache(self, flat_layers: list[Any]) -> bool:
        """Return True when the leaf-only projection path is valid.

        Falls back to tree composite when *buffered* groups or non-stroke
        sibling effects are present — the leaf-only path would produce
        incorrect blending for those cases (spec §4.8.4).
        """
        for layer in flat_layers:
            lt = getattr(layer, "layer_type", "raster")
            if lt == "effect":
                return False
            if lt == "group" and not self._is_pass_through_inline(layer):
                return False
        return True

    def _composite_with_projection(
        self,
        canvas: Any,
        flat_layers: list[Any],
        parent_ids: list[str | None],
        active_flat_idx: int,
        cw: int,
        ch: int,
        content_changed: bool,
        effects_changed: bool,
        dirty_rect: Any | None,
        background: QColor | None = None,
    ) -> None:
        """Retired GL projection helper.

        The production projection cache is implemented by
        ``WgpuNativeCanvasPresenter`` / ``WgpuProjectionCache``.
        """
        _ = (
            canvas,
            flat_layers,
            parent_ids,
            active_flat_idx,
            cw,
            ch,
            content_changed,
            effects_changed,
            dirty_rect,
            background,
        )
        self._projection_valid = False
        self._below_projection_valid = False

    def _composite_full_tree(
        self,
        canvas: Any,
        cw: int,
        ch: int,
        *,
        layers: list[Any] | None = None,
        background: QColor | None = None,
    ) -> None:
        """Retired GL full-tree composite shim."""
        _ = (canvas, cw, ch, layers, background)
        self._projection_valid = False
        self._below_projection_valid = False

    def _compute_canvas_mvp(self, canvas_w: int, canvas_h: int) -> list[float]:
        """Build a 4x4 column-major MVP that maps the fullscreen quad to the
        canvas rectangle in physical pixel coordinates (respecting zoom/pan/DPI)."""
        t = self.viewport.get_transform()
        dpr = self.devicePixelRatioF()
        ww = float(self.width()) * dpr
        wh = float(self.height()) * dpr
        cw = float(canvas_w)
        ch = float(canvas_h)

        # Scale the viewport transform from logical to physical pixels.
        m11, m12 = t.m11() * dpr, t.m12() * dpr
        m21, m22 = t.m21() * dpr, t.m22() * dpr
        dx, dy = t.dx() * dpr, t.dy() * dpr

        # MVP = T_ndc * T_viewport * T_quad_to_canvas
        # T_quad_to_canvas: (-1,1) → (0, canvas_size)
        # T_viewport: QTransform (canvas → widget pixels)
        # T_ndc: widget pixels → GL NDC (Y flipped)
        a = m11 * cw / ww
        b = m21 * ch / ww
        c = -m12 * cw / wh
        d = -m22 * ch / wh
        tx = (m11 * cw + m21 * ch + 2.0 * dx) / ww - 1.0
        ty = -(m12 * cw + m22 * ch + 2.0 * dy) / wh + 1.0

        # Column-major 4x4
        return [
            a,
            c,
            0.0,
            0.0,
            b,
            d,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            tx,
            ty,
            0.0,
            1.0,
        ]

    def _paint_cpu(self) -> None:
        """CPU fallback rendering (same as original paintEvent)."""
        perf_start = time.perf_counter() if _PEN_LAG_DIAG else 0.0

        painter = QPainter(self)

        if self.state.antialiasing:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        painter.setTransform(self.viewport.get_transform())

        cw, ch = self.state.width, self.state.height
        bg_transparent = self.state.background_color.alpha() < 255

        if bg_transparent:
            self._draw_checkerboard(painter, cw, ch)
        else:
            painter.fillRect(0, 0, cw, ch, self.state.background_color)

        host_pixmap = self._get_cpu_composited_pixmap()
        if host_pixmap is None and not self._force_cpu_composite:
            host_pixmap = self._get_host_pixmap()
        if host_pixmap:
            painter.drawPixmap(0, 0, host_pixmap)

        canvas = self._get_canvas_service()
        if canvas is not None:
            self._draw_mask_overlays(painter, canvas)

        self._draw_overlays(painter)
        painter.end()
        self._log_paint_perf("cpu", perf_start)

    def _log_paint_perf(self, path: str, start: float) -> None:
        if not _PEN_LAG_DIAG or start <= 0.0:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._paint_perf_frame += 1
        if elapsed_ms >= 12.0 or self._paint_perf_frame % 30 == 0:
            logger.warning(
                "pen-lag diag: paint path=%s frame=%d elapsed=%.2fms",
                path,
                self._paint_perf_frame,
                elapsed_ms,
            )

    def _get_checkerboard_tile(self) -> QPixmap:
        """Return a cached 2x2-cell checkerboard tile for transparent background."""
        if self._checkerboard_tile is not None:
            return self._checkerboard_tile
        cell = 8  # pixels per checker cell
        size = cell * 2
        tile = QPixmap(size, size)
        tile.fill(QColor(255, 255, 255))
        p = QPainter(tile)
        grey = QColor(204, 204, 204)
        p.fillRect(0, 0, cell, cell, grey)
        p.fillRect(cell, cell, cell, cell, grey)
        p.end()
        self._checkerboard_tile = tile
        return tile

    def _draw_checkerboard(self, painter: QPainter, w: int, h: int) -> None:
        """Draw a tiled checkerboard pattern on the canvas area."""
        tile = self._get_checkerboard_tile()
        painter.drawTiledPixmap(0, 0, w, h, tile)

    def _get_cpu_composited_pixmap(self) -> QPixmap | None:
        """Return a CPU-composited pixmap when the canvas service exposes one."""
        canvas = self._get_canvas_service()
        if canvas is None:
            return None
        getter = getattr(canvas, "get_cpu_composited_pixmap", None)
        if not callable(getter):
            return None
        try:
            return cast("QPixmap | None", getter())
        except Exception:
            logger.exception("CPU canvas compositing failed")
            return None

    def _draw_selection_preview(
        self,
        painter: QPainter,
        canvas: Any,
        *,
        canvas_transform: QTransform | None = None,
    ) -> None:
        """Draw selection preview image overlay (used by GPU path)."""
        preview_img = getattr(canvas, "_selection_preview_image", None)
        preview_bounds = getattr(canvas, "_selection_preview_bounds", None)
        if preview_img is not None and preview_bounds is not None:
            painter.setTransform(canvas_transform or self.viewport.get_transform())
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(int(preview_bounds[0]), int(preview_bounds[1]), preview_img)
            painter.resetTransform()

    def _draw_mask_overlays(self, painter: QPainter, canvas: Any) -> None:
        """Draw semi-transparent colored overlay for visible mask layers."""
        layers = getattr(canvas, "_layers", None)
        if not layers:
            return
        settings = getattr(self._host_api, "settings", None) if self._host_api else None
        color_hex = "#00FFFF"
        overlay_alpha = 128
        if settings is not None:
            get = getattr(settings, "get", None)
            if callable(get):
                color_hex = get("ui/mask_overlay_color", "#00FFFF")
                overlay_alpha = int(get("ui/mask_overlay_opacity", 128))
        overlay_color = QColor(color_hex)
        overlay_color.setAlpha(max(0, min(255, overlay_alpha)))

        for layer in layers:
            if not layer.visible or layer.layer_type != "mask" or layer.image is None:
                continue
            img = layer.image
            # Build colored overlay clipped by mask alpha
            overlay = QImage(img.width(), img.height(), QImage.Format.Format_ARGB32_Premultiplied)
            overlay.fill(overlay_color)
            p = QPainter(overlay)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            p.drawImage(0, 0, img)
            p.end()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(1.0)
            painter.drawImage(layer.offset_x, layer.offset_y, overlay)

    def _draw_overlays(self, painter: QPainter) -> None:
        """Draw grid, guides, buffers, and marching ants."""
        if self.state.grid_visible:
            self._draw_grid(painter)
        self._draw_guides(painter)
        self._draw_drawing_assist(painter)
        if self._tool_preview_image is not None:
            prev_mode = painter.compositionMode()
            prev_opacity = painter.opacity()
            painter.setCompositionMode(self._tool_preview_comp_mode)
            painter.setOpacity(prev_opacity * self._tool_preview_opacity)
            painter.drawImage(0, 0, self._tool_preview_image)
            painter.setCompositionMode(prev_mode)
            painter.setOpacity(prev_opacity)
        if self._overlay_buffer:
            painter.drawPixmap(0, 0, self._overlay_buffer)
        if self._temp_buffer and self._is_drawing and self._show_stroke_preview:
            painter.drawPixmap(0, 0, self._temp_buffer)
        self._draw_marching_ants(painter)
        self._draw_vector_selection_overlay(painter)
        if self._canvas_agent_overlay_fn is not None:
            self._canvas_agent_overlay_fn(painter)
        # Tool UI overlay (e.g. gradient line/pins) drawn last = topmost
        if self._tool_overlay_fn is not None:
            self._tool_overlay_fn(painter)

    def _draw_vector_selection_overlay(self, painter: QPainter) -> None:
        """Draw vector selection/editing UI in screen space (spec §15)."""
        canvas = self._get_canvas_service()
        if canvas is None:
            return
        draw_vector_selection_overlay(painter, canvas)

    # --- Layer texture synchronisation ---

    def _get_canvas_service(self) -> Any | None:
        """Return the canvas service if available."""
        if self._host_api is None:
            return None
        canvas = getattr(self._host_api, "canvas", None)
        if canvas is None:
            return None
        if hasattr(canvas, "get_canvas_state_snapshot") or hasattr(canvas, "_layers"):
            return canvas
        return None

    def _consume_render_invalidation(self, canvas: Any) -> RenderInvalidation:
        consume = getattr(canvas, "consume_render_invalidation", None)
        if callable(consume):
            return cast(RenderInvalidation, consume())
        return RenderInvalidation(
            dirty_rect=canvas.consume_gpu_dirty_rect(),
            full_recompose=canvas.consume_gpu_full_recompose(),
            mask_dirty_layer_ids=frozenset(canvas.consume_gpu_mask_dirty_layers()),
        )

    def _sync_layer_textures(  # noqa: PLR0912
        self,
        canvas: Any,
        active_idx: int = -1,
        flat_layers: list[Any] | None = None,
        *,
        dirty_rect: Any | None = ...,
        full_recompose: bool | None = None,
    ) -> bool:
        """Upload dirty layer textures to GPU.

        Returns True if any texture was updated (compositing needed).
        If *flat_layers* is provided, use it instead of re-flattening.
        *dirty_rect* / *full_recompose* can be pre-consumed by the caller;
        when omitted (sentinel ``...``/``None``), they are consumed here.
        """
        if full_recompose is None:
            full_recompose = canvas.consume_gpu_full_recompose()
        if dirty_rect is ...:
            dirty_rect = canvas.consume_gpu_dirty_rect()
        current_ids: set[str] = set()
        any_changed = full_recompose or (dirty_rect is not None)

        # Use pre-flattened list or flatten now
        if flat_layers is None:
            flat_layers = self._flatten_all_layers(canvas._layers)

        for i, layer in enumerate(flat_layers):
            lid = layer.id
            current_ids.add(lid)

            if self._layer_tex_versions.get(lid) == layer.texture_version:
                # Texture unchanged — but sync offset so move-drag preview
                # renders at the correct position without re-uploading pixels.
                self._render_backend.set_layer_offset(
                    lid,
                    int(getattr(layer, "offset_x", 0)),
                    int(getattr(layer, "offset_y", 0)),
                )
                continue  # Unchanged — skip

            # Get image (rasterize vector layers)
            if layer.layer_type == "vector":
                if self._rasterize_vector_gpu(canvas, layer):
                    self._layer_tex_versions[lid] = layer.texture_version
                    if self._gl_effect_processor.is_initialized:
                        self._gl_effect_processor.mark_cache_stale(lid)
                    any_changed = True
                    continue
                # CPU fallback
                rasterize = getattr(canvas, "_rasterize_vector_layer", None)
                if callable(rasterize):
                    img = rasterize(layer)
                else:
                    continue
            elif layer.layer_type == "group":
                continue  # Groups have no image; children are already flattened
            else:
                img = layer.image

            if img is None:
                continue

            is_active = i == active_idx

            # Use sub-region upload for the active layer when a dirty rect
            # is available (pen strokes).  This avoids uploading the entire
            # canvas-sized texture (~16 MB @ 2000x2000) every frame.
            # Safety: sub-upload requires (a) existing GPU texture whose
            # size matches the current image (post _ensure_layer_image_bounds
            # expansions invalidate this), and (b) dirty_rect translated
            # from canvas-space to the layer's local coordinates.
            existing_entry = self._gl_compositor._layer_textures.get(lid)
            sub_upload_ok = (
                is_active
                and dirty_rect is not None
                and not full_recompose
                and existing_entry is not None
                and existing_entry[1] == img.width()
                and existing_entry[2] == img.height()
            )
            if sub_upload_ok and dirty_rect is not None:
                local_dirty = QRect(cast(QRect, dirty_rect))
                local_dirty.translate(
                    -int(getattr(layer, "offset_x", 0)),
                    -int(getattr(layer, "offset_y", 0)),
                )
                self._render_backend.update_layer_texture_region(lid, img, local_dirty)
            else:
                self._render_backend.update_layer_texture(lid, img, layer.offset_x, layer.offset_y)

            self._layer_tex_versions[lid] = layer.texture_version
            # Mark effect cache stale (keeps GPU texture for reuse)
            if self._gl_effect_processor.is_initialized:
                self._gl_effect_processor.mark_cache_stale(lid)
            any_changed = True
            if is_active and getattr(canvas, "_in_transaction", False):
                _tx_cnt = getattr(self, "_sync_active_cnt", 0) + 1
                self._sync_active_cnt = _tx_cnt
                if _tx_cnt <= 3 or _tx_cnt % 30 == 0:
                    logger.debug(
                        "_sync_layer_textures: active layer upload #%d" " ver=%d dirty_rect=%s",
                        _tx_cnt,
                        layer.texture_version,
                        dirty_rect is not None,
                    )

        # Remove textures for layers that no longer exist
        stale = set(self._layer_tex_versions) - current_ids
        for lid in stale:
            self._delete_vector_fbo(lid)
            self._render_backend.remove_layer_texture(lid)
            del self._layer_tex_versions[lid]
            self._gl_layer_compositor.remove_mask_texture(lid)

        return any_changed

    def _apply_gpu_effects(
        self,
        canvas: Any,
        flat_layers: list[Any],
        *,
        mask_dirty_layers: set[str] | None = None,
    ) -> bool:
        """Apply GPU effects and layer masks to raster/vector layers.

        Returns ``True`` if any effected texture was created or updated,
        meaning the compositor must re-composite even when layer content
        itself has not changed.

        Real-time stroke preview: ``_sync_layer_textures`` calls
        ``mark_cache_stale`` whenever a layer texture is updated, so the
        effect cache is invalidated at the same point raster strokes
        upload dirty sub-regions.  This lets effects re-run every frame
        for both vector edits and raster strokes, giving consistent
        live effect previews.
        """
        _ = (canvas, flat_layers, mask_dirty_layers)
        return False

    # ------------------------------------------------------------------
    # Vector layer GPU rasterization
    # ------------------------------------------------------------------

    def _rasterize_vector_gpu(self, canvas: Any, layer: Any) -> bool:  # noqa: PLR0911
        """Retired GL vector texture path.

        Returns ``True`` on success, ``False`` to fall back to CPU path.
        """
        _ = (canvas, layer)
        return False

    @staticmethod
    def _create_vector_fbo(w: int, h: int) -> tuple[int, int]:
        """Retired GL vector FBO shim."""
        _ = (w, h)
        return (0, 0)

    def _delete_vector_fbo(self, layer_id: str) -> None:
        """Retired GL vector FBO cleanup shim."""
        self._vector_fbos.pop(layer_id, None)

    def _cleanup_vector_fbos(self) -> None:
        """Delete all vector FBOs."""
        for lid in list(self._vector_fbos):
            self._delete_vector_fbo(lid)

    @staticmethod
    def _flatten_all_layers(layers: list[Any]) -> list[Any]:
        """Return visible tree order without using the retired GL compositor."""

        flat: list[Any] = []

        def visit(items: list[Any]) -> None:
            for item in items:
                flat.append(item)
                children = getattr(item, "children", None)
                if children:
                    visit(list(children))

        visit(layers)
        return flat

    # ------------------------------------------------------------------
    # Recursive GPU compositing compatibility shims.
    # ------------------------------------------------------------------

    def _composite_layers_gpu(
        self,
        layers: list[Any],
        fbos: list[tuple[int, int]],
        src_idx: int,
        canvas_w: int,
        canvas_h: int,
        depth: int = 0,
    ) -> int:
        """Retired GL tree composite shim."""
        del layers, fbos, canvas_w, canvas_h, depth
        return src_idx

    def _apply_sibling_effect_gpu(
        self,
        layer: Any,
        fbos: list[tuple[int, int]],
        src_idx: int,
        canvas_w: int,
        canvas_h: int,
        prev_layer_tex: int = 0,
    ) -> int:
        """Retired GL sibling effect shim."""
        del layer, fbos, canvas_w, canvas_h, prev_layer_tex
        return src_idx

    def _composite_group_gpu(
        self,
        group: Any,
        parent_fbos: list[tuple[int, int]],
        parent_src_idx: int,
        canvas_w: int,
        canvas_h: int,
        clip_alpha_tex: int = 0,
        clip_rect: tuple[float, float, float, float] | None = None,
        depth: int = 0,
    ) -> int:
        """Retired GL group composite shim."""
        del group, parent_fbos, canvas_w, canvas_h, clip_alpha_tex, clip_rect, depth
        return parent_src_idx

    @staticmethod
    def _is_pass_through_inline(layer: Any) -> bool:
        """Return whether a group should inline its children."""

        blend = str(getattr(layer, "blend_mode", "normal")).replace("-", "_").lower()
        return blend in {"pass_through", "passthrough", "normal"}

    # --- Qt Event Handlers ---

    def _invalidate_wgpu_bitmap_resources(self, reason: str) -> None:
        self._wgpu_bitmap().invalidate_resources(reason)

    def showEvent(self, event: Any) -> None:
        """Handle show event."""
        super().showEvent(event)
        self._invalidate_wgpu_bitmap_resources("widget shown")

        # Initialize buffers when first shown
        if not self._buffers_initialized:
            size = self.size()
            if size.width() > 0 and size.height() > 0:
                self._initialize_buffers()
                self.viewport.set_widget_size(size.width(), size.height())
                # SSO Phase 1 (R-1): overlay も logical widget size を知る必要がある
                self._overlay_manager.set_widget_size(size.width(), size.height())

    def resizeEvent(self, event: Any) -> None:
        """Handle resize event."""
        super().resizeEvent(event)

        new_size = event.size()
        old_size = event.oldSize()

        # Only process valid sizes
        if new_size.width() <= 0 or new_size.height() <= 0:
            return

        if new_size != old_size:
            logger.debug(f"Canvas resized: {new_size.width()}x{new_size.height()}")
            self._invalidate_wgpu_bitmap_resources("widget resized")

            # Reinitialize buffers
            self._initialize_buffers()

            # Update viewport with valid size
            self.viewport.set_widget_size(new_size.width(), new_size.height())

            # SSO Phase 1 (R-1): resizeGL は空関数のため resizeEvent で hook。
            # set_widget_size 内部で entries/preview_image をクリアして次フレーム再生成。
            self._overlay_manager.set_widget_size(new_size.width(), new_size.height())

            # Emit signal
            self.canvas_resized.emit(new_size.width(), new_size.height())

    def hideEvent(self, event: Any) -> None:
        """Handle hide/minimize resource release for optional presenters."""
        self._invalidate_wgpu_bitmap_resources("widget hidden")
        super().hideEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        """Track DPI/screen changes for optional presenter resources."""
        super().changeEvent(event)
        dpr_event = getattr(QEvent.Type, "DevicePixelRatioChange", None)
        screen_event = getattr(QEvent.Type, "ScreenChangeInternal", None)
        window_state_event = getattr(QEvent.Type, "WindowStateChange", None)
        if event.type() in {dpr_event, screen_event}:
            self._invalidate_wgpu_bitmap_resources("device pixel ratio or screen changed")
        elif event.type() == window_state_event:
            self._invalidate_wgpu_bitmap_resources("window state changed")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press."""
        if not self._input_handler_intercepts(
            event
        ) and self._three_d_interaction.handle_mouse_press(event):
            return
        if (
            self._guide_controller
            and event.button() == Qt.MouseButton.LeftButton
            and self._active_tool_can_move_guides()
            and not self._input_handler_intercepts(event)
        ):
            pos = event.position()
            canvas_pos = self.viewport.widget_to_canvas(pos.x(), pos.y())
            if self._guide_controller.handle_mouse_press(
                event,
                QPointF(float(canvas_pos[0]), float(canvas_pos[1])),
            ):
                event.accept()
                return
        self.input_handler.handle_mouse_press(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move."""
        if self._three_d_interaction.handle_mouse_move(event):
            return
        if (
            self._guide_controller
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._active_tool_can_move_guides()
            and not self._input_handler_intercepts(event)
        ):
            pos = event.position()
            canvas_pos = self.viewport.widget_to_canvas(pos.x(), pos.y())
            if self._guide_controller.handle_mouse_move(
                event,
                QPointF(float(canvas_pos[0]), float(canvas_pos[1])),
            ):
                event.accept()
                return
        self.input_handler.handle_mouse_move(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release."""
        if self._three_d_interaction.handle_mouse_release(event):
            return
        if (
            self._guide_controller
            and event.button() == Qt.MouseButton.LeftButton
            and self._active_tool_can_move_guides()
            and not self._input_handler_intercepts(event)
        ):
            pos = event.position()
            canvas_pos = self.viewport.widget_to_canvas(pos.x(), pos.y())
            if self._guide_controller.handle_mouse_release(
                event,
                QPointF(float(canvas_pos[0]), float(canvas_pos[1])),
            ):
                event.accept()
                return
        self.input_handler.handle_mouse_release(event)

    #: plugin payload drops forwarded as ``ui.canvas.drop`` events
    #: (shape manager shapes, comic assets); the payload carries the mime
    _PLUGIN_DROP_MIME_TYPES = (
        "application/x-loomstroke-shape+json",
        "application/x-loomstroke-comic-asset+json",
    )

    def _plugin_drop_mime(self, mime_data: Any) -> str | None:
        has_format = getattr(mime_data, "hasFormat", None)
        if not callable(has_format):
            return None
        for mime in self._PLUGIN_DROP_MIME_TYPES:
            if has_format(mime):
                return mime
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime_data = event.mimeData()
        if self._extract_image_file_paths(mime_data):
            event.acceptProposedAction()
            return
        if self._plugin_drop_mime(event.mimeData()) is not None:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._extract_image_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        if self._plugin_drop_mime(event.mimeData()) is not None:
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime_data = event.mimeData()
        image_paths = self._extract_image_file_paths(mime_data)
        if image_paths:
            from .panel_image_drop import dispatch_panel_image_drop  # noqa: PLC0415

            if dispatch_panel_image_drop(self, event, image_paths):
                event.acceptProposedAction()
                return
            imported = self._import_images_as_layers(image_paths)
            if imported:
                event.acceptProposedAction()
            else:
                event.ignore()
            return

        plugin_mime = self._plugin_drop_mime(event.mimeData())
        if plugin_mime is not None:
            data = bytes(cast(Any, event.mimeData().data(plugin_mime)))
            payload_json = data.decode("utf-8", errors="ignore")
            pos = event.position()
            canvas_pos = self.viewport.widget_to_canvas(pos.x(), pos.y())
            mods = event.modifiers()
            payload = {
                "mime": plugin_mime,
                "pos_canvas": (float(canvas_pos[0]), float(canvas_pos[1])),
                "payload_json": payload_json,
                "modifiers": {
                    "shift": bool(mods & Qt.KeyboardModifier.ShiftModifier),
                    "alt": bool(mods & Qt.KeyboardModifier.AltModifier),
                    "ctrl": bool(mods & Qt.KeyboardModifier.ControlModifier),
                },
            }
            self._emit_canvas_drop(payload)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handle wheel event for zoom."""
        if self._three_d_interaction.handle_wheel(event):
            return
        # Get wheel delta
        delta = event.angleDelta().y() / 120.0

        # Calculate zoom factor
        zoom_factor = 1.1 if delta > 0 else 0.9

        # Get mouse position in widget coords
        pos = event.position()

        # Zoom at mouse position
        self.viewport.zoom_at(pos.x(), pos.y(), zoom_factor)

        # Schedule update
        self.update()

    def tabletEvent(self, event: QTabletEvent) -> None:
        """Handle tablet event."""
        self.input_handler.handle_tablet_event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle key press."""
        # Pass to input handler first for modifier tracking
        self.input_handler.handle_key_press(event)
        if event.key() == Qt.Key.Key_Space:
            return

        if self._guide_controller and self._guide_controller.handle_key_press(event):
            event.accept()
            return

        # Context toolbar intercepts Enter/Esc before tool dispatch (§7.1)
        if self._context_toolbar_manager is not None:
            key_name = self._normalize_tool_key(event)
            if key_name and self._context_toolbar_manager.handle_key(key_name, pressed=True):
                event.accept()
                return

        self._dispatch_tool_key(event, pressed=True)

        # Reset view on Home
        if event.key() == Qt.Key.Key_Home:
            self.viewport.reset()
            self.update()

        # Fit to window on F
        elif event.key() == Qt.Key.Key_F:
            self.fit_to_window()

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        """Handle key release."""
        # Pass to input handler for modifier tracking
        self.input_handler.handle_key_release(event)
        if event.key() == Qt.Key.Key_Space:
            return

        # Context toolbar symmetric release interception
        if self._context_toolbar_manager is not None:
            key_name = self._normalize_tool_key(event)
            if key_name and self._context_toolbar_manager.handle_key(key_name, pressed=False):
                event.accept()
                return

        self._dispatch_tool_key(event, pressed=False)
        super().keyReleaseEvent(event)

    # Canvas Operations

    def _on_pointer_down(self, point: StrokePoint) -> None:
        """Handle pointer down from input handler."""
        self._is_drawing = True
        self._pending_canvas_update = False
        self._pending_stroke_points.clear()
        self._stroke_move_emitted = False
        self._current_stroke = [point]

        # Start update timer for smooth drawing
        if not self._update_timer.isActive():
            self._update_timer.start()

        # Emit signal for tool
        self.stroke_started.emit(point)

    def _on_pointer_move(self, points: list[StrokePoint]) -> None:
        """Handle pointer move from input handler."""
        if self._is_drawing:
            self._current_stroke.extend(points)

            # Draw to temp buffer
            if self._show_stroke_preview:
                self._draw_stroke_segment(points)

            # Mark dirty region
            self._mark_dirty(points)

            # Emit the first move immediately so the stroke appears at once;
            # coalesce subsequent high-frequency input into the 16ms timer.
            if not self._stroke_move_emitted:
                self._stroke_move_emitted = True
                self.stroke_updated.emit(points)
            else:
                self._pending_stroke_points.extend(points)

    def _on_pointer_up(self, point: StrokePoint) -> None:
        """Handle pointer up from input handler."""
        if self._is_drawing:
            self._is_drawing = False
            self._current_stroke.append(point)
            self._flush_pending_stroke_points()

            # Stop update timer
            self._update_timer.stop()
            self._pending_canvas_update = False

            # Commit temp buffer to display
            self._commit_stroke()

            # Clear stroke buffer
            self._current_stroke.clear()

            # Final update
            self.update()

            # Emit signal for tool
            self.stroke_ended.emit(point)

    def _on_pointer_hover(self, point: StrokePoint) -> None:
        """Handle pointer hover from input handler."""
        self.pointer_hovered.emit(point)

    def _draw_stroke_segment(self, points: list[StrokePoint]) -> None:
        """Draw stroke segment to temp buffer."""
        if not self._temp_buffer or len(points) < 1:
            return

        painter = QPainter(self._temp_buffer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Simple line drawing for now
        pen = QPen(QColor(0, 0, 0), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        # Draw lines between points
        for i in range(len(points) - 1):
            p1 = self.viewport.canvas_to_widget(points[i].pos[0], points[i].pos[1])
            p2 = self.viewport.canvas_to_widget(points[i + 1].pos[0], points[i + 1].pos[1])
            painter.drawLine(QPointF(p1[0], p1[1]), QPointF(p2[0], p2[1]))

        painter.end()

    def _commit_stroke(self) -> None:
        """Clear preview buffer - actual drawing is handled by Host."""
        if self._temp_buffer:
            self._temp_buffer.fill(Qt.GlobalColor.transparent)

    def _mark_dirty(self, points: list[StrokePoint]) -> None:
        """Mark dirty region for update."""
        for point in points:
            widget_pos = self.viewport.canvas_to_widget(point.pos[0], point.pos[1])
            rect = QRectF(widget_pos[0] - 10, widget_pos[1] - 10, 20, 20)
            self._dirty_region = self._dirty_region.united(rect)

    def _deferred_update(self) -> None:
        """Deferred update for performance."""
        self._flush_pending_stroke_points()
        if self._pending_canvas_update:
            self._pending_canvas_update = False
            self._dirty_region = QRectF()
            self.update()
        elif not self._dirty_region.isEmpty():
            self.update(self._dirty_region.toRect())
            self._dirty_region = QRectF()

    def _flush_pending_stroke_points(self) -> None:
        if not self._pending_stroke_points:
            return
        points = list(self._pending_stroke_points)
        self._pending_stroke_points.clear()
        self.stroke_updated.emit(points)

    def _on_viewport_changed(self, state: dict[str, Any]) -> None:
        """Handle viewport change."""
        self.viewport_changed.emit(state)
        self.update()

    def _draw_grid(self, painter: QPainter) -> None:
        """Draw grid overlay with pixel-snapped lines for uniform cells."""
        if not self.state.grid_visible:
            return

        painter.save()

        step = self.state.grid_size
        if step <= 0:
            step = 1

        # Compute grid line positions in canvas coordinates
        canvas_rect = QRectF(0.0, 0.0, float(self.state.width), float(self.state.height))
        visible_rect = QRectF(self.viewport.get_visible_rect()).intersected(canvas_rect)
        if visible_rect.isEmpty():
            painter.restore()
            return

        # Switch to widget coordinates for pixel-perfect lines
        transform = painter.transform()
        painter.resetTransform()

        pen = QPen(QColor(200, 200, 200), 1.0)
        painter.setPen(pen)

        # Map canvas clip rect to widget coords for clipping
        widget_clip = transform.mapRect(canvas_rect)
        painter.setClipRect(widget_clip)

        # Map canvas boundary edges to widget coords
        top_left_w = transform.map(QPointF(0.0, 0.0))
        bot_right_w = transform.map(QPointF(float(self.state.width), float(self.state.height)))
        w_left = top_left_w.x()
        w_right = bot_right_w.x()
        w_top = top_left_w.y()
        w_bottom = bot_right_w.y()

        # Draw vertical lines — snap each to nearest pixel
        left = visible_rect.left()
        right = visible_rect.right()
        first_xi = int(left) // step
        last_xi = int(right) // step + 1
        for xi in range(first_xi, last_xi + 1):
            pt = transform.map(QPointF(float(xi * step), 0.0))
            wx = round(pt.x()) + 0.5
            painter.drawLine(QPointF(wx, w_top), QPointF(wx, w_bottom))

        # Draw horizontal lines — snap each to nearest pixel
        top = visible_rect.top()
        bottom = visible_rect.bottom()
        first_yi = int(top) // step
        last_yi = int(bottom) // step + 1
        for yi in range(first_yi, last_yi + 1):
            pt = transform.map(QPointF(0.0, float(yi * step)))
            wy = round(pt.y()) + 0.5
            painter.drawLine(QPointF(w_left, wy), QPointF(w_right, wy))

        painter.restore()

    def _draw_guides(self, painter: QPainter) -> None:
        if self._guide_controller is None:
            return
        self._guide_controller.draw(painter)

    def _draw_drawing_assist(self, painter: QPainter) -> None:
        # CT-14: non-printing assist overlay, attached by the Drawing Assist plugin.
        controller = getattr(self, "_drawing_assist_controller", None)
        if controller is not None:
            controller.draw(painter)

    # Public API

    def request_context_menu(self, widget_pos: tuple[float, float]) -> None:
        pos = QPoint(int(widget_pos[0]), int(widget_pos[1]))
        global_pos = self.mapToGlobal(pos)
        canvas_pos = self.viewport.widget_to_canvas(widget_pos[0], widget_pos[1])
        payload = {
            "global_pos": global_pos,
            "widget_pos": (float(widget_pos[0]), float(widget_pos[1])),
            "canvas_pos": (float(canvas_pos[0]), float(canvas_pos[1])),
        }
        self.context_menu_requested.emit(payload)

    def fit_to_window(self) -> None:
        """Fit canvas to window."""
        self.viewport.fit_to_content(self.state.width, self.state.height)
        self.update()

    def set_canvas_size(self, width: int, height: int) -> None:
        """Set canvas size."""
        self.state.width = width
        self.state.height = height
        self._initialize_buffers()
        self.update()

    def set_grid_size(self, size: int) -> None:
        """Set grid spacing in canvas coordinates."""
        try:
            step = int(size)
        except (TypeError, ValueError):
            logger.debug("Invalid grid size: %r", size)
            return

        if step <= 0:
            step = 1

        self.state.grid_size = step
        self.update()

    def toggle_grid(self) -> None:
        """Toggle grid visibility."""
        self.state.grid_visible = not self.state.grid_visible
        self.update()

    def set_tool_preview(
        self,
        image: QImage | None,
        composition_mode: QPainter.CompositionMode | None = None,
        opacity: float = 1.0,
    ) -> None:
        """Set or clear the tool preview overlay image (canvas coordinates)."""
        self._tool_preview_image = image
        self._tool_preview_comp_mode = (
            composition_mode
            if composition_mode is not None
            else QPainter.CompositionMode.CompositionMode_SourceOver
        )
        self._tool_preview_opacity = max(0.0, min(1.0, opacity))
        self.update()

    def set_tool_overlay_fn(self, fn: Any) -> None:
        """Set or clear a callable(QPainter) drawn after the tool preview image."""
        self._tool_overlay_fn = fn
        self.update()

    def set_canvas_agent_overlay_fn(self, fn: Any) -> None:
        """Set or clear a runtime-only Canvas Agent preview overlay."""
        self._canvas_agent_overlay_fn = fn
        self.update()

    def clear_canvas(self) -> None:
        """Clear canvas content."""
        if self._overlay_buffer:
            self._overlay_buffer.fill(Qt.GlobalColor.transparent)
        if self._temp_buffer:
            self._temp_buffer.fill(Qt.GlobalColor.transparent)
        # Clear active layer in Host
        if self._host_api and hasattr(self._host_api.canvas, "clear_layer"):
            self._host_api.canvas.clear_layer()
        self.update()

    def set_host_api(self, host_api: HostAPI | None) -> None:
        """Set the host API reference.

        Args:
            host_api: HostAPI instance
        """
        self._host_api = host_api

        # Connect to canvas service signals if available
        if host_api and hasattr(host_api.canvas, "composited_changed"):
            host_api.canvas.composited_changed.connect(self._on_composited_changed)

        # Bitmap compositing uses wgpu even though the retired GL flag stays false.
        # Preview must treat composited_changed pixmaps as GPU invalidation signals.
        if host_api and self._wgpu_bitmap_requested and not self._force_cpu_composite:
            canvas = getattr(host_api, "canvas", None)
            if canvas and hasattr(canvas, "set_gpu_rendering"):
                canvas.set_gpu_rendering(True)

    def set_guide_controller(self, controller: GuideController | None) -> None:
        self._guide_controller = controller

    def set_context_toolbar_manager(self, manager: Any) -> None:
        """Set the ContextToolbarManager for key interception."""
        self._context_toolbar_manager = manager

    def _input_handler_intercepts(self, event: QMouseEvent) -> bool:
        pan_check = getattr(self.input_handler, "is_pan_gesture", None)
        if callable(pan_check) and pan_check(event):
            return True
        ctx_check = getattr(self.input_handler, "is_context_menu_gesture", None)
        return bool(callable(ctx_check) and ctx_check(event))

    def flush_and_readback(self) -> QPixmap | None:
        """Force a GPU render, then read back the composited result.

        Export/save paths call this to guarantee the returned pixmap
        reflects the latest layer state. Pending texture uploads and
        effect recomputes are flushed synchronously before readback,
        which ``get_gpu_composited_pixmap`` alone does not guarantee.

        Returns ``None`` when the active compatibility backend cannot
        provide a readback or the canvas service is unavailable.
        """
        canvas = self._get_canvas_service()
        if canvas is None:
            return None
        return self._flush_wgpu_bitmap_readback(canvas)

    def _flush_wgpu_bitmap_readback(self, canvas: Any) -> QPixmap | None:
        if not self._wgpu_bitmap_requested:
            self._wgpu_bitmap_requested = True
        try:
            if not self._paint_wgpu_bitmap(canvas, paint_widget=False):
                return None
        except Exception:
            logger.exception("flush_and_readback: wgpu bitmap repaint failed")
            return None
        return self.get_gpu_composited_pixmap()

    def get_gpu_composited_pixmap(self) -> QPixmap | None:
        """Read back the current GPU composited result as a QPixmap.

        Uses the wgpu bitmap presenter export image when it has a valid
        cached frame.  Otherwise uses the projection cache FBO when valid,
        then falls back to the regular ping-pong result FBO.  Returns
        ``None`` when the active backend cannot provide a readback.
        """
        wgpu_pixmap = self._get_wgpu_bitmap_pixmap_for_export()
        if wgpu_pixmap is not None:
            return wgpu_pixmap
        return None

    def _get_wgpu_bitmap_pixmap_for_export(self) -> QPixmap | None:
        return self._wgpu_bitmap().pixmap_for_export()

    def _get_host_pixmap(self) -> QPixmap | None:
        """Get composited pixmap from Host.

        Returns:
            Composited pixmap or None if not available
        """
        if self._host_api and hasattr(self._host_api.canvas, "get_composited_pixmap"):
            try:
                return cast("QPixmap | None", self._host_api.canvas.get_composited_pixmap())
            except Exception as error:
                logger.debug("Could not get host pixmap: %s", error)
        return None

    def _on_composited_changed(self, pixmap: QPixmap) -> None:
        """Handle composited image change from Host.

        Args:
            pixmap: New composited pixmap
        """
        # Update canvas dimensions and background from service
        canvas = self._get_canvas_service()
        if canvas is not None:
            state = (
                canvas.get_canvas_state_snapshot()
                if hasattr(canvas, "get_canvas_state_snapshot")
                else None
            )
            if state is not None:
                self.state.width = state.width
                self.state.height = state.height
                self.state.background_color = state.background
            else:
                self.state.width = canvas._width
                self.state.height = canvas._height
                self.state.background_color = getattr(
                    canvas, "_background", self.state.background_color
                )
        else:
            try:
                self.state.width = int(pixmap.width())
                self.state.height = int(pixmap.height())
            except (TypeError, ValueError, AttributeError):
                logger.debug("canvas pixmap size probe failed", exc_info=True)
        self._sync_marching_ants_timer()
        if self._is_drawing:
            self._pending_canvas_update = True
            return
        self.update()

    # --- Marching-ants selection animation ---

    def _sync_marching_ants_timer(self) -> None:
        """Start or stop the marching-ants timer based on selection state."""
        # Invalidate outline cache so next paint rebuilds it
        self._ants_path_cache = None
        self._ants_path_mask_key = None
        has_sel = self._has_selection()
        if has_sel and not self._marching_ants_timer.isActive():
            self._marching_ants_timer.start()
        elif not has_sel and self._marching_ants_timer.isActive():
            self._marching_ants_timer.stop()
            self._marching_ants_offset = 0.0

    def _has_selection(self) -> bool:
        if not self._host_api:
            return False
        canvas = self._host_api.canvas
        mask = getattr(canvas, "_selection_mask", None)
        bounds = getattr(canvas, "_selection_bounds", None)
        if mask is None or bounds is None:
            return False
        return not bounds.isEmpty()

    def _tick_marching_ants(self) -> None:
        """Advance the marching-ants dash offset and repaint."""
        self._marching_ants_offset = (self._marching_ants_offset + 1.0) % 16.0
        self.update()

    def _draw_marching_ants(self, painter: QPainter) -> None:
        """Draw animated marching-ants border around the selection.

        Photoshop-style: traces the pixel-level boundary of the mask
        rather than drawing a simple bounding rect.
        """
        if not self._host_api:
            return
        canvas = self._host_api.canvas
        mask = getattr(canvas, "_selection_mask", None)
        bounds = getattr(canvas, "_selection_bounds", None)
        if mask is None or bounds is None:
            return
        if bounds.isEmpty():
            return

        painter.save()

        path = self._get_selection_outline_path(mask)
        if path is None or path.isEmpty():
            path = QPainterPath()
            path.addRect(bounds)

        pen_black = QPen(QColor(0, 0, 0), 1, Qt.PenStyle.SolidLine)
        pen_black.setCosmetic(True)
        painter.setPen(pen_black)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        pen_white = QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine)
        pen_white.setCosmetic(True)
        pen_white.setDashOffset(self._marching_ants_offset)
        painter.setPen(pen_white)
        painter.drawPath(path)

        painter.restore()

    def _get_selection_outline_path(self, mask: QImage) -> QPainterPath | None:  # noqa: PLR0912
        """Build (and cache) a QPainterPath tracing the pixel boundary of *mask*.

        The path consists of horizontal and vertical line segments along
        every edge between a selected and an unselected pixel — the same
        visual style as Photoshop's marching ants.
        """
        import numpy as np  # noqa: PLC0415

        w, h = mask.width(), mask.height()
        if w <= 0 or h <= 0:
            return None

        # Cache key: use cacheKey() which changes when image data changes
        key = mask.cacheKey()
        if self._ants_path_mask_key == key and self._ants_path_cache is not None:
            return self._ants_path_cache

        # Read mask data — Alpha8 format, one byte per pixel
        # Use bits() which returns a writable sip.voidptr (more reliable
        # than constBits on some PySide6 versions).
        src = mask
        if src.format() != QImage.Format.Format_Alpha8:
            src = src.convertToFormat(QImage.Format.Format_Alpha8)
        bpl = src.bytesPerLine()
        total_bytes = bpl * h
        ptr = src.bits()
        if ptr is None:
            return None
        if hasattr(ptr, "setsize"):
            ptr.setsize(total_bytes)
        try:
            raw = bytes(ptr)
        except Exception:
            return None
        sel = np.frombuffer(raw, dtype=np.uint8).reshape(h, bpl)[:, :w] > 127

        if not np.any(sel):
            self._ants_path_cache = None
            self._ants_path_mask_key = key
            return None

        # Pad with False border so boundary detection includes image edges
        padded = np.zeros((h + 2, w + 2), dtype=bool)
        padded[1 : h + 1, 1 : w + 1] = sel

        path = QPainterPath()

        # Horizontal edges: between row r and row r+1 in padded
        hdiff = padded[:-1, :] != padded[1:, :]  # shape (h+1, w+2)
        for y in range(h + 1):
            row = hdiff[y, 1 : w + 1]  # only interior columns
            if not np.any(row):
                continue
            changes = np.diff(np.concatenate(([False], row, [False])).astype(np.int8))
            starts = np.where(changes == 1)[0]
            ends = np.where(changes == -1)[0]
            for s, e in zip(starts, ends, strict=False):
                path.moveTo(float(s), float(y))
                path.lineTo(float(e), float(y))

        # Vertical edges: between col c and col c+1 in padded
        vdiff = padded[:, :-1] != padded[:, 1:]  # shape (h+2, w+1)
        for x in range(w + 1):
            col = vdiff[1 : h + 1, x]  # only interior rows
            if not np.any(col):
                continue
            changes = np.diff(np.concatenate(([False], col, [False])).astype(np.int8))
            starts = np.where(changes == 1)[0]
            ends = np.where(changes == -1)[0]
            for s, e in zip(starts, ends, strict=False):
                path.moveTo(float(x), float(s))
                path.lineTo(float(x), float(e))

        self._ants_path_cache = path
        self._ants_path_mask_key = key
        return path

    def _dispatch_tool_key(self, event: QKeyEvent, *, pressed: bool) -> None:
        if not self._host_api:
            return
        tool_manager = getattr(self._host_api, "_tool_manager", None)
        if tool_manager is None or not hasattr(tool_manager, "dispatch_key_event"):
            return
        key = self._normalize_tool_key(event)
        if key is None:
            return
        tool_manager.dispatch_key_event(key, pressed)

    def _active_tool_can_move_guides(self) -> bool:
        return self._active_tool_id() in _GUIDE_MOVE_TOOL_IDS

    def _active_tool_id(self) -> str | None:
        if not self._host_api:
            return None
        tool_manager = getattr(self._host_api, "_tool_manager", None) or getattr(
            self._host_api,
            "tool_manager",
            None,
        )
        if tool_manager is None:
            return None
        getter = getattr(tool_manager, "get_active_tool", None)
        if callable(getter):
            descriptor = getter()
            tool_id = getattr(descriptor, "id", None)
            if isinstance(tool_id, str):
                return tool_id
        tool_id = getattr(tool_manager, "_active_tool_id", None)
        return tool_id if isinstance(tool_id, str) else None

    def _normalize_tool_key(self, event: QKeyEvent) -> str | None:
        key = event.key()
        direct_key_map: dict[Any, str] = {
            Qt.Key.Key_Escape: "esc",
            Qt.Key.Key_Return: "return",
            Qt.Key.Key_Enter: "enter",
            Qt.Key.Key_Control: "ctrl",
            Qt.Key.Key_Shift: "shift",
            Qt.Key.Key_Alt: "alt",
        }
        direct_key = direct_key_map.get(key)
        if direct_key is not None:
            return cast("str | None", direct_key)

        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(ord("a") + (key - Qt.Key.Key_A))
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(ord("0") + (key - Qt.Key.Key_0))
        text = event.text()
        if text:
            return text.lower()
        return None

    def _emit_canvas_drop(self, payload: dict[str, Any]) -> None:
        if not self._host_api:
            return
        bus = getattr(self._host_api, "events", None)
        emit = getattr(bus, "emit", None) if bus else None
        if not callable(emit):
            emit = getattr(bus, "publish", None) if bus else None
        if callable(emit):
            try:
                emit("ui.canvas.drop", payload)
            except Exception:
                logger.debug("Canvas drop emit failed", exc_info=True)

    def _extract_image_file_paths(self, mime_data: Any) -> list[str]:
        extract = getattr(mime_data, "urls", None)
        if not callable(extract):
            return []

        paths: list[str] = []
        for url in extract():
            to_local = getattr(url, "toLocalFile", None)
            if not callable(to_local):
                continue
            local_path = str(to_local())
            if not local_path:
                continue
            if self._is_image_file(local_path):
                paths.append(local_path)
        return paths

    def _import_images_as_layers(self, image_paths: list[str]) -> int:
        if not self._host_api:
            return 0
        document = getattr(self._host_api, "document", None)
        importer = getattr(document, "import_layer", None) if document else None
        if not callable(importer):
            logger.debug("HostAPI.document.import_layer is unavailable")
            return 0

        imported_count = 0
        for path in image_paths:
            try:
                layer_id = importer(path, insert_above=True, as_new=True)
                if layer_id is None:
                    logger.warning("Failed to import image file as layer: %s", path)
                    continue
                imported_count += 1
                logger.info("Imported image file as new layer: %s", path)
            except Exception:
                logger.debug("Failed to import dropped image file: %s", path, exc_info=True)
        return imported_count

    def _is_image_file(self, path: str) -> bool:
        file_path = Path(path)
        if not file_path.is_file():
            return False
        try:
            if QImageReader.imageFormat(file_path.as_posix()):
                return True
        except (OSError, RuntimeError):
            logger.debug("QImageReader format probe failed for %s", file_path, exc_info=True)
        suffix = file_path.suffix.lower().lstrip(".")
        return suffix in _IMAGE_FILE_EXTENSIONS
