"""Default dock panel bootstrap coordination for MainWindow."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QLabel, QListWidget

from .main_window_channels_panel import MainWindowChannelsPanelBootstrap
from .panels.layer_panel import LayerPanel
from .panels.preview.preview_panel import PreviewPanel
from .panels.tool_panel import FloatingToolToolbarFrame

logger = logging.getLogger(__name__)


class MainWindowDefaultPanelsCoordinator:
    """Coordinate creation of MainWindow's built-in dock panels."""

    def __init__(
        self,
        window: Any,
        *,
        panel_min_width: int,
        single_shot: Callable[[int, Callable[[], None]], None],
    ) -> None:
        self._window = window
        self._panel_min_width = panel_min_width
        self._single_shot = single_shot

    def initialize_state(self) -> None:
        self._window._extra_panels_initialized = False
        self._window._preview_panel = None
        self._window._production_control_panel = None
        self._window._canvas_copilot_panel = None

    def setup_default_panels(self) -> None:
        """Set up default dock panels."""
        self.setup_layer_panel()
        MainWindowChannelsPanelBootstrap.setup(self._window)
        self.setup_preview_panel()
        self.setup_tool_list_panel()
        self.setup_additional_panels()

    def setup_layer_panel(self) -> None:
        window = self._window
        layer_dock = QDockWidget("Layers", window)
        try:
            layer_widget = LayerPanel(window.host)
            if hasattr(layer_widget, "set_host_api"):
                layer_widget.set_host_api(window.host)

            # Prefer ID-based signals. Index-based signals are legacy-only
            # compatibility fallback and should not be used by real LayerPanel.
            self._connect_first_available_signal(
                layer_widget,
                ("layer_visibility_changed_by_id", "layer_visibility_changed"),
                (
                    window._on_layer_visibility_changed_by_id,
                    window._on_layer_visibility_changed,
                ),
            )
            self._connect_first_available_signal(
                layer_widget,
                ("layer_locked_changed_by_id", "layer_locked_changed"),
                (
                    window._on_layer_locked_changed_by_id,
                    window._on_layer_locked_changed,
                ),
            )
            self._connect_first_available_signal(
                layer_widget,
                ("layer_alpha_lock_changed_by_id",),
                (window._on_layer_alpha_lock_changed_by_id,),
            )
            self._connect_first_available_signal(
                layer_widget,
                ("layer_opacity_changed_by_id", "layer_opacity_changed"),
                (
                    window._on_layer_opacity_changed_by_id,
                    window._on_layer_opacity_changed,
                ),
            )
            self._connect_first_available_signal(
                layer_widget,
                ("layer_selected_by_id", "layer_selected"),
                (window._on_layer_selected_by_id, window._on_layer_selected),
            )
            self._connect_first_available_signal(
                layer_widget,
                ("layer_added",),
                (window._on_layer_added,),
            )
            self._connect_first_available_signal(
                layer_widget,
                ("mask_layer_added",),
                (window._on_mask_layer_added,),
            )
            self._connect_first_available_signal(
                layer_widget,
                ("layer_deleted_by_id", "layer_deleted"),
                (window._on_layer_deleted_by_id, window._on_layer_deleted),
            )
            self._connect_first_available_signal(
                layer_widget,
                ("layer_duplicated",),
                (window._on_layer_dup,),
            )

            window._layer_panel = layer_widget
            logger.info("Using LayerPanel implementation")
        except ImportError:
            layer_widget = cast("Any", QListWidget())
            layer_widget.addItems(["Background", "Layer 1", "Layer 2"])
            logger.info("Using placeholder for Layers panel")
        layer_dock.setWidget(layer_widget)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, layer_dock)
        window.register_panel("ui.layers", "Layers", layer_dock, default_visible=True)

    @staticmethod
    def _connect_first_available_signal(
        widget: Any,
        signal_names: tuple[str, ...],
        slots: tuple[Callable[..., Any], ...],
    ) -> None:
        for signal_name, slot in zip(signal_names, slots, strict=True):
            signal = getattr(widget, signal_name, None)
            if signal is not None and hasattr(signal, "connect"):
                signal.connect(slot)
                return

    def setup_preview_panel(self) -> None:
        window = self._window
        preview_dock = QDockWidget("Preview", window)
        try:
            preview_widget = PreviewPanel(window, window._event_bus)
            window._preview_panel = preview_widget
            preview_widget.zoom_changed.connect(window._on_preview_zoom_changed)
            preview_widget.fit_requested.connect(window._on_preview_fit_requested)
            preview_widget.reset_zoom_requested.connect(window._on_preview_reset_zoom_requested)
            preview_widget.viewport_drag_requested.connect(window._on_preview_drag_requested)
            preview_widget.image_displayed.connect(window._on_preview_image_displayed)
            logger.info("Using PreviewPanel implementation")
        except ImportError as exc:
            logger.warning("PreviewPanel import failed: %s", exc)
            preview_widget = cast("Any", QLabel("Preview Area\n(ComfyUI preview will appear here)"))
            preview_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview_widget.setStyleSheet("background-color: #333; color: #aaa;")
            logger.info("Using placeholder for Preview panel")
        preview_dock.setWidget(preview_widget)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, preview_dock)
        window.register_panel("ui.preview", "Preview", preview_dock, default_visible=True)
        window._sync_preview_from_canvas()
        window._sync_preview_controls_from_canvas()

    def setup_tool_list_panel(self) -> None:
        window = self._window
        try:
            frame = FloatingToolToolbarFrame(window.host, window)
            window._floating_tool_toolbar = frame
            window._tool_list_panel = frame.toolbar
            frame.set_host_api(window.host)
            self._single_shot(0, window._restore_initial_floating_tool_toolbar_state)
            logger.info("Using floating ToolToolbar implementation")
        except Exception:
            logger.exception("Failed to create floating ToolToolbar")

    def setup_additional_panels(self) -> None:
        """Set up additional panels if implementations are available."""
        window = self._window
        if window._extra_panels_initialized:
            return

        if not window._plugin_registry:
            logger.debug("Plugin registry not yet available; deferring additional panel setup")
            return

        window._setup_page_panel_if_available()
        window._setup_color_panel_if_available()
        window._setup_text_font_panel_if_available()
        window._setup_shape_manager_panel_if_available()
        window._setup_prompt_editor_panel_if_available()
        window._setup_nano_banana_panel_if_available()
        window._setup_gptimage2_panel_if_available()
        window._setup_comfyui_panel_if_available()
        window._setup_gallery_panel_if_available()
        self.setup_canvas_copilot_panel_if_available()
        window._setup_production_control_panel_if_available()
        self.setup_comic_project_panel_if_available()
        self.setup_links_panel_if_available()
        self.setup_actions_panel_if_available()
        self.setup_drawing_assist_panel_if_available()
        self.setup_comic_workflow_panel_if_available()

        window._extra_panels_initialized = True

        logger.info("Building Window menu with registered panels...")
        window._build_window_menu()

    def setup_page_panel_if_available(self) -> None:
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "page_panel",
                area_override="left",
                title_context="MainWindow",
                success_message="Pages panel added successfully",
            )
        except Exception as exc:
            logger.error("Failed to add Pages panel: %s", exc, exc_info=True)

    def setup_comic_project_panel_if_available(self) -> None:
        # CT-17: story / dialogue / production management (right dock).
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "comic_project_panel",
                area_override="right",
                title_context="ComicProjectPanel",
            )
        except Exception as exc:
            logger.error("Failed to add Comic Project panel: %s", exc, exc_info=True)

    def setup_links_panel_if_available(self) -> None:
        # CT-18: linked / embedded resources (right dock, hidden by default).
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "links_panel",
                area_override="right",
                title_context="LinksPanel",
            )
        except Exception as exc:
            logger.error("Failed to add Links panel: %s", exc, exc_info=True)

    def setup_actions_panel_if_available(self) -> None:
        # CT-20: declarative actions / macros / batch (right dock).
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "actions_panel",
                area_override="right",
                title_context="ActionsPanel",
            )
        except Exception as exc:
            logger.error("Failed to add Actions panel: %s", exc, exc_info=True)

    def setup_drawing_assist_panel_if_available(self) -> None:
        # CT-14: symmetry / ruler / perspective assists (right dock).
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "drawing_assist_panel",
                area_override="right",
                title_context="DrawingAssistPanel",
            )
        except Exception as exc:
            logger.error("Failed to add Drawing Assist panel: %s", exc, exc_info=True)

    def setup_comic_workflow_panel_if_available(self) -> None:
        # CT-22: comic production workspace (right dock).
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "comic_workflow_panel",
                area_override="right",
                title_context="ComicWorkflowPanel",
            )
        except Exception as exc:
            logger.error("Failed to add Comic Workflow panel: %s", exc, exc_info=True)

    def setup_color_panel_if_available(self) -> None:
        window = self._window
        try:
            window._setup_ui_panel_plugin_if_available(
                "color_panel",
                area_override="left",
                title_context="MainWindow",
                before_register=lambda: setattr(
                    window, "_color_panel_min_width", self._panel_min_width
                ),
                after_register=window._sync_color_panel_min_width,
            )
        except Exception as exc:
            logger.error("Failed to add Color panel: %s", exc, exc_info=True)

    def setup_text_font_panel_if_available(self) -> None:
        window = self._window
        try:
            window._setup_ui_panel_plugin_if_available(
                "text_font_panel",
                area_override="left",
                title_context="TextFontPanel",
                after_register=window._sync_color_panel_min_width,
            )
        except Exception as exc:
            logger.error("Failed to add Font panel: %s", exc, exc_info=True)

    def setup_shape_manager_panel_if_available(self) -> None:
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "loomstroke.shape_manager",
                area_override="right",
                title_context="ShapeManagerPlugin",
                success_message="Shape Manager panel added successfully",
            )
        except Exception as exc:
            logger.error("Failed to add Shape Manager panel: %s", exc, exc_info=True)

    def setup_prompt_editor_panel_if_available(self) -> None:
        window = self._window
        try:
            prompt_editor_plugin = window._plugin_registry.get("prompt_editor_panel")
            if not prompt_editor_plugin:
                logger.info("Prompt Editor plugin not in registry, attempting direct import...")
                module = importlib.import_module("loomstroke.plugins.prompt_editor")
                create_plugin = getattr(module, "create_plugin", None)
                if create_plugin is None:
                    raise AttributeError("Prompt Editor plugin missing create_plugin")
                prompt_editor_plugin = create_plugin()
                logger.info("Prompt Editor plugin loaded via fallback")
            window._setup_ui_panel_plugin(
                prompt_editor_plugin,
                "prompt_editor_panel",
                area_override="right",
                title_context="MainWindow",
                success_message="Prompt Editor panel added successfully",
            )
        except (ImportError, AttributeError) as exc:
            logger.error("Failed to import Prompt Editor plugin: %s", exc)
        except Exception as exc:
            logger.error("Failed to add Prompt Editor panel: %s", exc, exc_info=True)

    def setup_nano_banana_panel_if_available(self) -> None:
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "nano_banana_panel",
                area_override="right",
                title_context="MainWindow",
            )
        except Exception as exc:
            logger.error("Failed to add NanoBanana panel: %s", exc, exc_info=True)

    def setup_gptimage2_panel_if_available(self) -> None:
        try:
            self._window._setup_ui_panel_plugin_if_available(
                "gptimage2_panel",
                area_override="right",
                title_context="MainWindow",
            )
        except Exception as exc:
            logger.error("Failed to add GPT Image 2 panel: %s", exc, exc_info=True)

    def setup_production_control_panel_if_available(self) -> None:
        window = self._window
        try:
            if window.has_panel("ui.production_control"):
                return
            from .panels.production_control_panel import ProductionControlPanel  # noqa: PLC0415

            controller = getattr(window, "_mcp_runtime_controller", None)
            service = getattr(controller, "service", None)
            panel = ProductionControlPanel(service, window)
            dock = QDockWidget("Production Control(incomplete)", window)
            dock.setWidget(panel)
            window._production_control_panel = panel
            window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            window.register_panel(
                "ui.production_control",
                "Production Control(incomplete)",
                dock,
                default_visible=True,
                title_context="MainWindow",
            )
            logger.info("Production Control panel added successfully")
        except Exception as exc:
            logger.error("Failed to add Production Control panel: %s", exc, exc_info=True)

    def setup_canvas_copilot_panel_if_available(self) -> None:
        window = self._window
        try:
            if window.has_panel("ui.canvas_copilot"):
                return
            from .panels.canvas_copilot_panel import CanvasCopilotPanel  # noqa: PLC0415

            controller = getattr(window, "_mcp_runtime_controller", None)
            service = getattr(controller, "service", None)
            panel = CanvasCopilotPanel(service, window)
            dock = QDockWidget("Canvas Assistant(incomplete)", window)
            dock.setWidget(panel)
            window._canvas_copilot_panel = panel
            window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            window.register_panel(
                "ui.canvas_copilot",
                "Canvas Assistant(incomplete)",
                dock,
                default_visible=False,
                title_context="MainWindow",
            )
            logger.info("Canvas Copilot panel added successfully")
        except Exception as exc:
            logger.error("Failed to add Canvas Copilot panel: %s", exc, exc_info=True)
