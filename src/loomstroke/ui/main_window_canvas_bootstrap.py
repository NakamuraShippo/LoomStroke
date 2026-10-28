"""Canvas bootstrap and stroke-event coordination for MainWindow."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import Any, cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QStackedWidget

from ..core.book_spread_layout import BookLayout
from ..core.tool_manager import ToolManager
from ..host_api import StrokePoint
from ..platform.settings_schema import DEFAULT_SETTINGS
from .canvas.canvas_viewport_host import CanvasViewportHost
from .canvas.canvas_widget import CanvasWidget, _select_render_backend
from .canvas.guide_controller import GuideController
from .canvas.page_view_state_controller import PageViewStateController
from .canvas.render_scene import (
    set_direct_vector_rendering_enabled,
    set_view_resolution_compositing_enabled,
)
from .canvas.ruler_frame import RulerCanvasFrame
from .canvas.spread_companion_pass import spread_quality_scale
from .canvas.spread_view_controller import SpreadViewController
from .main_window_preview_sync import MainWindowPreviewSyncCoordinator
from .startpage_panel import StartPagePanel

logger = logging.getLogger("loomstroke.ui.main_window")


class MainWindowCanvasBootstrapCoordinator:
    """Coordinate MainWindow canvas startup wiring and stroke side effects."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def initialize_state(self) -> None:
        window = self._window
        window._workspace_stack = None
        window._startpage_panel = None
        window._canvas_frame = None
        window._context_toolbar_manager = None
        # Held by the window: these controllers subscribe to the event bus,
        # which keeps handlers weakly.
        window._spread_view_controller = None
        window._page_view_state_controller = None

    def setup_canvas(self) -> None:
        """Set up the central canvas widget while preserving existing ordering."""
        window = self._window
        try:
            # Phase 3: migrate away from the deprecated GPU toggle keys.
            # GPU rendering is mandatory — any stored user preference to
            # disable it is now silently removed with a warning log so the
            # settings file stays in sync with the runtime behaviour.
            self.migrate_deprecated_gpu_settings()
            # Try to use actual canvas implementation
            window.canvas = self._create_canvas_widget()
            self._apply_overlay_settings()

            # Set host API in canvas (MainWindow stores it as self.host)
            if window.host:
                self._wire_host_canvas_and_tools()

            logger.info("Using CanvasWidget for central area with ToolManager")
        except ImportError as e:
            self._install_canvas_placeholder(e)
        self._setup_workspace_stack()

    def _create_canvas_widget(self) -> Any:
        window = self._window
        if _select_render_backend(os.environ.get("LOOMSTROKE_RENDER_BACKEND")) == "wgpu_native":
            return CanvasViewportHost(window)
        return CanvasWidget(window)

    def _apply_overlay_settings(self) -> None:
        window = self._window
        # SSO Phase 1: 起動時に settings から overlay 有効/cap 値を反映
        # (仕様 §9.1 — Beta 期は opt-in 配信、デフォルト False)
        try:
            window.canvas.set_overlay_max_dim(
                int(cast(Any, window._settings.get("performance/vector_overlay_max_dim", 4096)))
            )
            window.canvas.set_overlay_enabled(
                window._settings.get_bool("performance/vector_overlay_enabled", False)
            )
            # Phase 1b: PD-3 計測 (Q-3 実測) の ON/OFF
            window.canvas.set_overlay_debug_timing(
                window._settings.get_bool("performance/vector_overlay_debug_timing", False)
            )
        except Exception:
            logger.debug("SSO overlay settings application failed", exc_info=True)
        # Direct WGPU vector rendering (retained-mode, spec §27):
        # settings キーまたは LOOMSTROKE_DIRECT_VECTOR_RENDERING で有効化。
        # SettingsManager.get() はキー未保存時に呼び出し側の default を返す
        # (DEFAULT_SETTINGS を参照しない) ため、schema 既定値をここで渡す。
        try:
            set_direct_vector_rendering_enabled(
                window._settings.get_bool(
                    "performance/direct_vector_rendering_enabled",
                    bool(DEFAULT_SETTINGS.get("performance/direct_vector_rendering_enabled", True)),
                )
            )
            set_view_resolution_compositing_enabled(
                window._settings.get_bool(
                    "performance/view_resolution_compositing_enabled",
                    bool(
                        DEFAULT_SETTINGS.get(
                            "performance/view_resolution_compositing_enabled", False
                        )
                    ),
                )
            )
        except Exception:
            logger.debug("direct vector rendering settings application failed", exc_info=True)

    def _wire_host_canvas_and_tools(self) -> None:
        self._attach_canvas_to_host()
        self._setup_guide_and_ruler_frame()
        self._setup_spread_view()
        self._setup_page_view_state()
        self._setup_preview_sync()
        self._setup_tool_manager()
        self._connect_canvas_and_tool_signals()
        self._connect_canvas_service_preview_signal()

    def _attach_canvas_to_host(self) -> None:
        window = self._window
        window.canvas.set_host_api(window.host)
        attach_widget = getattr(window.host, "attach_canvas_widget", None)
        if callable(attach_widget):
            attach_widget(window.canvas)
        # Phase 2: register the canvas widget on the canvas service so
        # get_composited_pixmap() can flush + readback from the GPU.
        canvas_service = getattr(window.host, "canvas", None)
        svc_attach = getattr(canvas_service, "attach_canvas_widget", None)
        if callable(svc_attach):
            svc_attach(window.canvas)

    def _setup_guide_and_ruler_frame(self) -> None:
        window = self._window
        guide_controller = GuideController(
            canvas=window.canvas,
            host=window.host,
            settings=window._settings,
        )
        window.canvas.set_guide_controller(guide_controller)
        window._canvas_frame = RulerCanvasFrame(
            canvas=window.canvas,
            guide_controller=guide_controller,
            settings=window._settings,
            parent=window,
        )
        window.canvas.viewport_changed.connect(window._on_canvas_viewport_changed)
        window.canvas.canvas_resized.connect(window._on_canvas_canvas_resized)

    def _setup_spread_view(self) -> None:
        """Wire the spread viewport (CT-06 B4-c4).

        The controller follows the Pages panel's Single/Spread combo through
        the event bus, so the panel plugin never touches the canvas.  The
        binding direction comes from the document's book metadata, which is
        what the panel's binding control writes.
        """
        window = self._window
        set_controller = getattr(window.canvas, "set_spread_controller", None)
        if not callable(set_controller):
            # Bitmap fallback canvas: single page only.
            return
        page_service = getattr(window.host, "pages", None)
        if page_service is None:
            return
        window._spread_view_controller = SpreadViewController(
            page_service=page_service,
            event_bus=window._event_bus,
            book_layout_provider=self._book_layout_provider,
            quality_scale_provider=self._spread_quality_scale_provider,
        )
        set_controller(window._spread_view_controller)

    def _setup_page_view_state(self) -> None:
        """Keep each page's zoom and pan across page switches (CT-06 B4-c6)."""

        window = self._window
        page_service = getattr(window.host, "pages", None)
        if page_service is None:
            return
        canvas = window.canvas
        if getattr(canvas, "viewport", None) is None:
            return
        window._page_view_state_controller = PageViewStateController(
            page_service=page_service,
            viewport_provider=lambda: window.canvas.viewport,
            event_bus=window._event_bus,
            on_restored=self._request_canvas_repaint,
            enabled_provider=self._per_page_zoom_enabled,
            spread_anchor_provider=self._spread_anchor,
            active_page_rect_provider=self._active_page_canvas_rect,
        )

    def _spread_anchor(self) -> tuple[tuple[str, ...], float, float] | None:
        controller = self._window._spread_view_controller
        if controller is None:
            return None
        return controller.anchor()

    def _active_page_canvas_rect(self) -> tuple[float, float, float, float] | None:
        controller = self._window._spread_view_controller
        if controller is None:
            return None
        state = controller.state()
        return state.active_canvas_rect() if state is not None else None

    def _per_page_zoom_enabled(self) -> bool:
        return bool(
            self._window._settings.get_bool(
                "view/per_page_zoom_enabled",
                bool(DEFAULT_SETTINGS.get("view/per_page_zoom_enabled", False)),
            )
        )

    def _request_canvas_repaint(self) -> None:
        canvas = getattr(self._window, "canvas", None)
        if canvas is None:
            return
        refresh = getattr(canvas, "request_native_surface_refresh", None)
        try:
            if callable(refresh):
                refresh()
            canvas.update()
        except Exception:
            logger.debug("canvas repaint request failed", exc_info=True)

    def _spread_quality_scale_provider(self) -> float:
        return spread_quality_scale(
            self._window._settings.get(
                "performance/spread_inactive_page_quality",
                DEFAULT_SETTINGS.get("performance/spread_inactive_page_quality", "full"),
            )
        )

    def _book_layout_provider(self) -> BookLayout:
        document_service = self._window._get_document_service()
        if document_service is None:
            return BookLayout()
        resolve = getattr(document_service, "resolve_book_layout", None)
        if not callable(resolve):
            return BookLayout()
        return cast(BookLayout, resolve())

    def _setup_preview_sync(self) -> None:
        window = self._window
        window._is_canvas_stroke_in_progress = False
        window._preview_sync_coordinator = MainWindowPreviewSyncCoordinator(
            window,
            single_shot=QTimer.singleShot,
        )
        window._preview_sync().reset_pending_sync()

    def _setup_tool_manager(self) -> None:
        window = self._window
        # Create tool manager
        window.tool_manager = ToolManager(window.host, event_bus=window._event_bus)
        # Bind ToolManager to HostAPI so plugins can register tools
        if hasattr(window.host, "set_tool_manager"):
            window.host.set_tool_manager(window.tool_manager)
        window._sync_tool_properties_panel_tool_manager()
        if hasattr(window.tool_manager, "tool_undo_state_changed"):
            window.tool_manager.tool_undo_state_changed.connect(window._on_tool_undo_state_changed)
        if hasattr(window.tool_manager, "tool_registered"):
            # Bind the QObject receiver so Qt disconnects on window destruction,
            # including pending notifications from a longer-lived ToolManager.
            window.tool_manager.tool_registered.connect(window._sync_tool_shortcut_bindings)

    def _connect_canvas_and_tool_signals(self) -> None:
        window = self._window
        # Tool registration moved to set_plugin_registry() to ensure timing is correct
        # Default tool activation also moved there

        # Connect canvas signals to tool manager
        self.connect_stroke_lifecycle_signals()
        window.canvas.pointer_hovered.connect(window.tool_manager.dispatch_pointer_hover)
        window.canvas.input_handler.modifier_changed.connect(
            window.tool_manager.update_modifier_state
        )
        window.canvas.context_menu_requested.connect(window._on_canvas_context_menu_requested)
        window.tool_manager.tool_activated.connect(window._on_tool_cursor_activated)
        window.tool_manager.tool_deactivated.connect(window._on_tool_cursor_deactivated)

        # Create context toolbar manager
        window._context_toolbar_manager = window._create_context_toolbar_manager()

        from loomstroke.platform.launch_options import ai_features_enabled  # noqa: PLC0415

        if not ai_features_enabled():
            return
        # Status-bar busy indicator for SAM3 worker / generation requests.
        try:
            from .sam3_status_indicator import Sam3BusyStatusIndicator  # noqa: PLC0415

            window._sam3_busy_status_indicator = Sam3BusyStatusIndicator(
                window,
                window._event_bus,
            )
        except Exception:
            logger.exception("Failed to install SAM3 busy status indicator")

        # SAM 3.1: optional worker auto-start (never loads the model onto the
        # GPU; readiness is re-verified and failures only log a warning).
        try:
            import threading  # noqa: PLC0415

            from loomstroke.sam31.runtime_config import (  # noqa: PLC0415
                build_sam31_runtime_config,
            )
            from loomstroke.sam31.server_manager import (  # noqa: PLC0415
                get_sam31_server_manager,
            )

            sam31_config = build_sam31_runtime_config(window._settings)
            if sam31_config.auto_start:
                threading.Thread(
                    target=get_sam31_server_manager().auto_start_if_configured,
                    args=(sam31_config,),
                    name="sam31-auto-start",
                    daemon=True,
                ).start()
        except Exception:
            logger.exception("SAM 3.1 auto-start scheduling failed")

    def connect_stroke_lifecycle_signals(self) -> None:
        """Connect stroke signals in the order the preview contract needs.

        ``_on_stroke_started`` runs BEFORE the tool pointer-down dispatch:
        the first stroke command can recompose and emit
        ``composited_changed`` synchronously, and at that point
        ``_is_canvas_stroke_in_progress`` must already be True so the event
        coalesces instead of triggering a preview readback (UI-PERF-01).

        On stroke end the tool dispatch stays FIRST: a tool that defers
        ``end_transaction`` via a zero timer queues it before the preview
        idle flush, and composited events emitted during the pointer-up
        dispatch still see the stroke as in progress.
        """

        window = self._window
        window.canvas.stroke_started.connect(window._on_stroke_started)
        window.canvas.stroke_started.connect(window.tool_manager.dispatch_pointer_down)
        window.canvas.stroke_updated.connect(window.tool_manager.dispatch_pointer_move)
        window.canvas.stroke_updated.connect(window._on_stroke_updated)
        window.canvas.stroke_ended.connect(window.tool_manager.dispatch_pointer_up)
        window.canvas.stroke_ended.connect(window._on_stroke_ended)

    def _connect_canvas_service_preview_signal(self) -> None:
        window = self._window
        # Connect Host's canvas service signal if available
        # NOTE: canvas.update() is already connected in canvas_widget.set_host_api()
        if hasattr(window.host.canvas, "composited_changed"):
            window.host.canvas.composited_changed.connect(window._on_canvas_composited_changed)

    def _install_canvas_placeholder(self, error: ImportError) -> None:
        window = self._window
        # Fallback to placeholder
        logger.warning(f"CanvasWidget import failed: {error}")
        window.canvas = cast(Any, QLabel("Canvas Placeholder\n(CanvasWidget not found)"))
        window.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        window.canvas.setStyleSheet("background-color: #2b2b2b; color: #888; font-size: 14px;")
        logger.warning("Using placeholder for canvas")

    def _setup_workspace_stack(self) -> None:
        window = self._window
        window._restore_view_preferences()
        window._startpage_panel = StartPagePanel(window.host, window)
        window._workspace_stack = QStackedWidget(window)
        window._workspace_stack.addWidget(window._startpage_panel)
        if window._canvas_frame is not None:
            window._workspace_stack.addWidget(window._canvas_frame)
        else:
            window._workspace_stack.addWidget(window.canvas)
        window.setCentralWidget(window._workspace_stack)
        window._set_document_present(False)
        logger.info("Workspace stack set as central widget")

    def on_stroke_started(self, point: StrokePoint) -> None:
        """Handle stroke start from canvas."""
        window = self._window
        window._is_canvas_stroke_in_progress = True
        window._preview_sync().reset_pending_sync()
        window._event_bus.emit("canvas.stroke.started", {"point": point})

    def on_stroke_updated(self, points: Iterable[StrokePoint]) -> None:
        """Handle stroke update from canvas."""
        self._window._event_bus.emit("canvas.stroke.updated", {"points": points})

    def on_stroke_ended(self, point: StrokePoint) -> None:
        """Handle stroke end from canvas."""
        window = self._window
        window._is_canvas_stroke_in_progress = False
        window._schedule_pending_thumbnail_flush()
        window._event_bus.emit("canvas.stroke.ended", {"point": point})

    def migrate_deprecated_gpu_settings(self) -> None:
        """Remove deprecated GPU toggle keys from the user settings file."""
        window = self._window
        settings = getattr(window, "_settings", None)
        if settings is None:
            return
        contains = getattr(settings, "contains", None)
        remove = getattr(settings, "remove", None)
        if not callable(contains) or not callable(remove):
            return
        for key in ("performance/use_gpu", "render/use_gpu"):
            try:
                if contains(key):
                    logger.warning(
                        "Deprecated setting %s detected; removing. "
                        "GPU rendering is mandatory and cannot be disabled.",
                        key,
                    )
                    remove(key)
            except Exception:
                logger.debug("Failed to migrate deprecated setting %s", key, exc_info=True)
