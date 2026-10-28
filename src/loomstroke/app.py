"""
Main application entry point with qasync integration.

This module provides the main application class with proper
async/await support per Master.md specifications.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

if TYPE_CHECKING:
    from .core.canvas_service_simple import SimpleCanvasService
    from .core.document_service import DocumentServiceImpl
    from .core.page_service import PageServiceImpl
    from .core.services import (
        MinimalHistoryService,
        MinimalInputService,
        MinimalSettingsService,
        MinimalUIService,
    )
    from .host_api import HostAPI

try:
    from qasync import QEventLoop
except ImportError:
    logging.critical("qasync is required. Install with: pip install qasync")
    sys.exit(1)

from . import app_ai_effect_coordinator as _ai_effect_coordinator
from . import app_window_ai_wiring
from .app_bootstrapper import ApplicationBootstrapper
from .app_plugin_system_coordinator import PluginSystemCoordinator
from .platform import AsyncManager, SettingsManager, get_async_manager, get_settings_manager
from .platform.debug_config import configure_logging
from .platform.launch_options import ai_features_enabled
from .plugins.sdk import PluginRegistry

# Configure logging before creating logger
configure_logging()

logger = logging.getLogger(__name__)

AIEffectCoordinator = _ai_effect_coordinator.AIEffectCoordinator
_AIEffectGalleryAdapter = _ai_effect_coordinator._AIEffectGalleryAdapter

_AI_EFFECT_MANAGER_PANEL_ID = "ui.ai_effect_manager"
_AI_EFFECT_MANAGER_PANEL_OWNER = "loomstroke.ai_effect"

builtin_plugins = [
    "loomstroke.plugins.basic_pen.pen_tool",
    "loomstroke.plugins.filter_pen.plugin",
    "loomstroke.plugins.basic_eraser.eraser_tool",
    "loomstroke.plugins.basic_fill_bucket.fill_bucket_tool",
    "loomstroke.plugins.basic_gradient.gradient_tool",
    "loomstroke.plugins.basic_move.move_tool",
    "loomstroke.plugins.text_tool.tool",
    "loomstroke.plugins.basic_selection.rect_tool",
    "loomstroke.plugins.magic_wand.magic_wand_tool",
    "loomstroke.plugins.pattern_fill.pattern_fill_tool",
    "loomstroke.plugins.sam3_ai_select",
    "loomstroke.plugins.comic_tools",
    "loomstroke.plugins.vector_tools.path_tool",
    "loomstroke.plugins.vector_tools.precise_tool",
    "loomstroke.plugins.vector_tools.edit_tool",
    "loomstroke.plugins.color_manager.color_panel_plugin",
    "loomstroke.plugins.color_manager.color_tool",
    "loomstroke.plugins.text_tool.font_panel_plugin",
    "loomstroke.plugins.shape_manager.plugin",
    "loomstroke.plugins.transform_panel.plugin",
    "loomstroke.plugins.filter_panel",
    # AIEffectPanelPlugin retired — its responsibilities
    # (param editing, slot binding, trigger, Generate) now
    # live inside FilterPanel (Steps 2-5 of the merger).
    # AI Effect layers are first-class effect layers per the
    # original design.
    "loomstroke.plugins.page_panel.panel_plugin",
    "loomstroke.plugins.comic_project.panel_plugin",
    "loomstroke.plugins.links_panel.panel_plugin",
    "loomstroke.plugins.actions_panel.panel_plugin",
    "loomstroke.plugins.drawing_assist.panel_plugin",
    "loomstroke.plugins.comic_workflow.panel_plugin",
    "loomstroke.plugins.comfyui.ui_panel_plugin",
    "loomstroke.plugins.llm_gateway.tool",
    "loomstroke.plugins.gptimage2.service",
    "loomstroke.plugins.gptimage2.panel_plugin",
    "loomstroke.plugins.nanobanana.service",
    "loomstroke.plugins.nanobanana.panel_plugin",
    "loomstroke.plugins.prompt_editor.panel_plugin",
    "loomstroke.plugins.reference_viewer",
    "loomstroke.plugins.three_d_generation.preview_panel",
]


def _bootstrapper_for(app: Any) -> ApplicationBootstrapper:
    coordinator = getattr(app, "_bootstrapper", None)
    if coordinator is None:
        coordinator = ApplicationBootstrapper(app)
        app._bootstrapper = coordinator
    return coordinator


def _plugin_system_for(app: Any) -> PluginSystemCoordinator:
    coordinator = getattr(app, "_plugin_system_coordinator", None)
    if coordinator is None:
        coordinator = PluginSystemCoordinator(app)
        app._plugin_system_coordinator = coordinator
    return coordinator


def _ai_effect_for(app: Any) -> AIEffectCoordinator:
    coordinator = getattr(app, "_ai_effect_coordinator", None)
    if coordinator is None:
        coordinator = AIEffectCoordinator(app)
        app._ai_effect_coordinator = coordinator
    return coordinator


class LoomStrokeApp:
    """
    Main application class with async support.

    Manages application lifecycle, plugin system, and UI components.
    """

    builtin_plugins = builtin_plugins

    def _get_ai_effect_attr(self, name: str) -> Any:
        return getattr(_ai_effect_for(self), name)

    def _set_ai_effect_attr(self, name: str, value: Any) -> None:
        setattr(_ai_effect_for(self), name, value)

    _ai_effect_preferences = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_preferences"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_preferences", value),
    )
    _ai_effect_state = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_state"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_state", value),
    )
    _ai_effect_state_path = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_state_path"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_state_path", value),
    )
    _ai_effect_controller = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_controller"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_controller", value),
    )
    _ai_effect_gen_controller = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_gen_controller"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_gen_controller", value),
    )
    _ai_effect_preset_library = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_preset_library"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_preset_library", value),
    )
    _ai_effect_gateway_registry = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_gateway_registry"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_gateway_registry", value),
    )
    _ai_effect_editor_window = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_editor_window"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_editor_window", value),
    )
    _ai_effect_manager_panel = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_manager_panel"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_manager_panel", value),
    )
    _ai_effect_preset_manager_window = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_preset_manager_window"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_preset_manager_window", value),
    )
    _ai_effect_telemetry = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_telemetry"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_telemetry", value),
    )
    _ai_effect_preview_emitter = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_preview_emitter"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_preview_emitter", value),
    )
    _ai_effect_toast_handler = property(
        lambda self: self._get_ai_effect_attr("_ai_effect_toast_handler"),
        lambda self, value: self._set_ai_effect_attr("_ai_effect_toast_handler", value),
    )
    _pending_tool_plugins = property(
        lambda self: _plugin_system_for(self)._pending_tool_plugins,
        lambda self, value: setattr(_plugin_system_for(self), "_pending_tool_plugins", value),
    )

    def __init__(self) -> None:
        """Initialize the application."""
        # Enable crash logging FIRST (before anything else)
        self._crash_log_file: Any | None = None  # kept open for faulthandler
        self._setup_crash_logging()

        self.app: QApplication | None = None
        self.main_window: QMainWindow | None = None
        self.event_loop: QEventLoop | None = None

        # Core components
        self.async_manager: AsyncManager | None = None
        self.settings: SettingsManager | None = None
        self.plugin_registry: PluginRegistry | None = None

        # Host services
        self._host_api: HostAPI | None = None
        self._canvas_service: SimpleCanvasService | None = None
        self._history_service: MinimalHistoryService | None = None
        self._input_service: MinimalInputService | None = None
        self._settings_service: MinimalSettingsService | None = None
        self._ui_service: MinimalUIService | None = None
        self._document_service: DocumentServiceImpl | None = None
        self._page_service: PageServiceImpl | None = None

        # AI Effect Layer subsystem (Phase 6-a — wiring only, UI comes later)
        self._set_ai_effect_attr("_ai_effect_preferences", None)
        self._set_ai_effect_attr("_ai_effect_state", None)
        # Path the OnboardingState round-trips to. Cached so the
        # first-gen banner hook can call ``state.save(path)`` after
        # flipping ``onboarding.first_gen_done``.
        self._set_ai_effect_attr("_ai_effect_state_path", None)
        self._set_ai_effect_attr("_ai_effect_controller", None)
        self._set_ai_effect_attr("_ai_effect_gen_controller", None)
        self._set_ai_effect_attr("_ai_effect_preset_library", None)
        self._set_ai_effect_attr("_ai_effect_gateway_registry", None)
        # Created on first standalone fallback launch; the normal app
        # path uses the post-restore AI Effects Manager dock.
        self._set_ai_effect_attr("_ai_effect_editor_window", None)
        # Docked AI Effects Manager panel. In the normal app path this
        # is registered before the persisted dock layout restore runs,
        # then kept hidden until the user opens it. The legacy
        # ``_ai_effect_preset_manager_window`` name is retained as an
        # alias for export-sync and reduced-host fallbacks.
        self._set_ai_effect_attr("_ai_effect_manager_panel", None)
        self._set_ai_effect_attr("_ai_effect_preset_manager_window", None)

        # Setup logging
        self._setup_logging()

        logger.info("LoomStroke application initialized")

    def _setup_crash_logging(self) -> None:
        _bootstrapper_for(self)._setup_crash_logging()

    def _setup_logging(self) -> None:
        _bootstrapper_for(self)._setup_logging()

    @staticmethod
    def _install_exception_hooks() -> None:
        ApplicationBootstrapper._install_exception_hooks()

    @staticmethod
    def _ts(text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(
            lambda source: QCoreApplication.translate("LoomStrokeApp", source),
            text,
        )

    def create_app(self) -> QApplication:
        return _bootstrapper_for(self).create_app()

    def _install_qt_message_handler(self) -> None:
        _bootstrapper_for(self)._install_qt_message_handler()

    def _install_dock_animation_blocker(self) -> None:
        _bootstrapper_for(self)._install_dock_animation_blocker()

    def create_event_loop(self) -> QEventLoop:
        return _bootstrapper_for(self).create_event_loop()

    async def initialize(self) -> None:
        """Initialize application components."""
        try:
            # BN:S-3 — 超高解像度素材(600DPI A3等)の読み込みに備え上限を緩和
            from PIL import Image as PILImage  # noqa: PLC0415

            PILImage.MAX_IMAGE_PIXELS = 300_000_000

            self.settings = get_settings_manager()
            await self.settings.initialize()
            logger.info("Settings manager initialized")
            self._apply_theme_setting()

            self.async_manager = get_async_manager()
            assert self.app is not None, "QApplication must be created before async init"
            await self.async_manager.initialize(self.app)
            logger.info("Async manager initialized")

            self.plugin_registry = PluginRegistry()
            await self.plugin_registry.initialize()
            logger.info("Plugin registry initialized")

            await self._create_host_api()
            logger.info("Host API created")

            await self._load_plugins()
            logger.info("Plugins loaded")

            await self._create_main_window()
            logger.info("Application initialization complete")

        except Exception as e:
            logger.error(f"Application initialization failed: {e}")
            raise

    def _apply_theme_setting(self) -> None:
        _bootstrapper_for(self)._apply_theme_setting()

    async def _create_host_api(self) -> None:  # noqa: PLR0915
        """Create HostAPI with all required services."""
        from PySide6.QtGui import QColor  # noqa: PLC0415

        from .core.canvas_service_simple import SimpleCanvasService  # noqa: PLC0415
        from .core.color_state import ColorDescriptor, ColorState  # noqa: PLC0415
        from .core.document_service import DocumentServiceImpl  # noqa: PLC0415
        from .core.event_bus_extensions import get_extended_event_bus  # noqa: PLC0415
        from .core.page_service import PageServiceImpl  # noqa: PLC0415
        from .core.services import (  # noqa: PLC0415
            MinimalHistoryService,
            MinimalInputService,
            MinimalSettingsService,
            MinimalUIService,
        )
        from .host_api import HostAPI  # noqa: PLC0415
        from .io.file_io import FileIO  # noqa: PLC0415
        from .io.layer_io import LayerIO  # noqa: PLC0415
        from .platform.shortcut_manager import (  # noqa: PLC0415
            ShortcutManager,
            ShortcutServiceAdapter,
        )

        event_bus = get_extended_event_bus()

        canvas_service = SimpleCanvasService(
            width=1920,
            height=1080,
            background=QColor(255, 255, 255),
        )
        if hasattr(canvas_service, "set_event_bus"):
            canvas_service.set_event_bus(event_bus)

        input_service = MinimalInputService()
        settings_service = MinimalSettingsService(self.settings)
        ui_service = MinimalUIService()
        # CT-05 H4d2: document-wide history memory trims run coalesced on
        # the Qt event loop's idle turn instead of synchronously.
        page_service = PageServiceImpl(
            canvas_service,
            event_bus,
            history_trim_scheduler=lambda callback: QTimer.singleShot(0, callback),
        )
        # CT-05 H5b: the router-backed history service unifies content +
        # book history behind the unchanged HistoryService contract, and
        # the notifier binding makes it the single HISTORY_STATE /
        # HISTORY_CHANGED publisher (canvas raw emission is rerouted).
        history_service = MinimalHistoryService(
            canvas_service,
            event_bus=event_bus,
            router=page_service._document_history_router,
            state_notifier=page_service._history_state_notifier,
            page_context_resolver=page_service._resolve_history_page_context,
        )
        page_service._bind_document_history_listener(
            history_service.notify_document_history_changed
        )
        file_io = FileIO(self.settings)
        layer_io = LayerIO(file_io)
        document_service = DocumentServiceImpl(
            canvas=canvas_service,
            layer_io=layer_io,
            file_io=file_io,
            event_bus=event_bus,
            page_service=page_service,
            ui_service_getter=ui_service.get_layout_service,
        )
        # CT-06 Phase B3-c2a: bind SetBookLayoutCommand's book-metadata
        # port now that document_service (and its DocumentMetadataService)
        # exist. Only the two limited callbacks cross the boundary --
        # page_service never holds a DocumentServiceImpl/
        # DocumentMetadataService reference.
        page_service._bind_book_metadata_port(
            document_service._metadata_service._apply_book_metadata_transition,
            document_service._metadata_service._restore_book_namespace,
        )
        page_service._bind_masters_metadata_port(
            document_service._metadata_service._apply_masters_metadata_transition,
            document_service._metadata_service._restore_masters_namespace,
        )
        from .core.speaker_style_service import bind_speaker_styles  # noqa: PLC0415

        bind_speaker_styles(page_service, document_service._metadata_service)
        assert self.settings is not None, "Settings must be initialized before HostAPI"
        shortcut_manager = ShortcutManager(self.settings)
        shortcut_service = ShortcutServiceAdapter(shortcut_manager)

        host_api = HostAPI(
            canvas=canvas_service,
            history=history_service,
            input=input_service,
            settings=settings_service,
            events=event_bus,
            document=document_service,
            pages=page_service,
            ui=ui_service,
            shortcuts=shortcut_service,
        )
        self._host_api = host_api

        color_state_data = settings_service.get("color/state", {})
        if isinstance(color_state_data, str):
            try:
                import json as _json  # noqa: PLC0415

                color_state_payload = _json.loads(color_state_data)
            except Exception:
                color_state_payload = {}
        else:
            color_state_payload = color_state_data

        try:
            color_state = (
                ColorState.from_dict(color_state_payload)
                if color_state_payload
                else ColorState(current=ColorDescriptor((0.0, 0.0, 0.0, 1.0)))
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to restore color state: %s", exc)
            color_state = ColorState(current=ColorDescriptor((0.0, 0.0, 0.0, 1.0)))

        host_api.attach_color_state(color_state)
        host_api.emit_color_state(source="bootstrap")

        # Generation history store
        from .services.generation_history_store import (  # noqa: PLC0415
            GenerationHistoryStore,
        )

        if ai_features_enabled():
            generation_history = GenerationHistoryStore()
            generation_history.load()
            self._generation_history = generation_history
            host_api.attach_generation_history(generation_history)

        # Autosave manager
        from .io.autosave import AutosaveManager  # noqa: PLC0415

        self._autosave_manager = AutosaveManager(
            settings=self.settings,
            document_service=document_service,
            # canvas param expects legacy Canvas type; SimpleCanvasService is
            # only stored for backward compat and never read by AutosaveManager.
            canvas=cast("Any", canvas_service),
            event_bus=event_bus,
        )
        self._autosave_manager.recovery_available.connect(self._on_recovery_available)
        self._autosave_manager.start()

        self._canvas_service = canvas_service
        self._history_service = history_service
        self._input_service = input_service
        self._settings_service = settings_service
        self._ui_service = ui_service
        self._document_service = document_service
        self._page_service = page_service

        # AI Effect Layer subsystem (Phase 6-a). Kept in a separate method so
        # its failure does not block the rest of startup.
        if not ai_features_enabled():
            return
        try:
            await self._init_ai_effect_subsystem()
        except Exception:  # noqa: BLE001 — isolate AI Effect from core startup
            # ``logger.exception`` (vs ``warning(... %s, exc)``) so the full
            # traceback lands in user_data/logs/ — silent init failure was
            # the most common cause of "Register to Manager succeeds in
            # the UI but presets disappear on restart" reports, because
            # a partial init left ``_ai_effect_preset_library`` None
            # while the editor was still usable for export-via-dialog.
            logger.exception("AI Effect subsystem init failed (continuing without it)")

    async def _init_ai_effect_subsystem(self) -> None:
        if not ai_features_enabled():
            return
        await _ai_effect_for(self)._init_ai_effect_subsystem()

    def _on_ai_effect_toast_event(self, payload: Any) -> None:
        _ai_effect_for(self)._on_ai_effect_toast_event(payload)

    def _wire_ai_effect_comfyui_gateway(self) -> None:
        _ai_effect_for(self)._wire_ai_effect_comfyui_gateway()

    def _wire_ai_effect_nanobanana_gateway(self) -> None:
        _ai_effect_for(self)._wire_ai_effect_nanobanana_gateway()

    def _wire_ai_effect_gptimage2_gateway(self) -> None:
        _ai_effect_for(self)._wire_ai_effect_gptimage2_gateway()

    def _ai_effect_register_target_dir(self) -> Any | None:
        return _ai_effect_for(self)._ai_effect_register_target_dir()

    def _can_host_ai_effect_manager_panel(self) -> bool:
        return _ai_effect_for(self)._can_host_ai_effect_manager_panel()

    def _register_ai_effect_manager_panel(self) -> Any | None:
        return _ai_effect_for(self)._register_ai_effect_manager_panel()

    def _ensure_ai_effect_manager_panel(self) -> Any | None:
        return _ai_effect_for(self)._ensure_ai_effect_manager_panel()

    def _show_ai_effect_manager_panel(self, panel: Any) -> None:
        _ai_effect_for(self)._show_ai_effect_manager_panel(panel)

    def open_effect_editor(self) -> Any | None:
        if not ai_features_enabled():
            return None
        return _ai_effect_for(self).open_effect_editor()

    def _on_effect_preset_exported(self, path: Any) -> None:
        _ai_effect_for(self)._on_effect_preset_exported(path)

    def open_effect_editor_with_preset(self, preset_id: str) -> None:
        if not ai_features_enabled():
            return
        _ai_effect_for(self).open_effect_editor_with_preset(preset_id)

    def open_preset_manager(self) -> None:
        if not ai_features_enabled():
            return
        _ai_effect_for(self).open_preset_manager()

    def _wire_ai_effect_preset_filter_menu(self) -> None:
        _ai_effect_for(self)._wire_ai_effect_preset_filter_menu()

    def _wire_filter_panel_ai_signals(self) -> None:
        _ai_effect_for(self)._wire_filter_panel_ai_signals()

    def _find_filter_panel_widget(self) -> Any:
        return _ai_effect_for(self)._find_filter_panel_widget()

    _wire_effect_panel_ai_signals = _wire_filter_panel_ai_signals
    _find_effect_panel_widget = _find_filter_panel_widget

    def _on_ai_effect_generate_requested(self, layer_id: str) -> None:
        _ai_effect_for(self)._on_ai_effect_generate_requested(layer_id)

    async def _run_ai_effect_generation(self, layer_id: str, state: Any, resolver: Any) -> None:
        await _ai_effect_for(self)._run_ai_effect_generation(layer_id, state, resolver)

    def _show_no_preset_dialog_safe(self, parent: Any) -> None:
        _ai_effect_for(self)._show_no_preset_dialog_safe(parent)

    def _show_no_gateway_dialog_safe(self) -> None:
        _ai_effect_for(self)._show_no_gateway_dialog_safe()

    def _maybe_show_first_gen_banner(self) -> None:
        _ai_effect_for(self)._maybe_show_first_gen_banner()

    def _notify_ai_effect_error(self, err: Any) -> None:
        _ai_effect_for(self)._notify_ai_effect_error(err)

    def _apply_ai_effect_outputs(self, layer_id: str, state: Any, outcome: Any) -> Any:
        return _ai_effect_for(self)._apply_ai_effect_outputs(layer_id, state, outcome)

    def _ai_effect_task_scheduler(self, coro: Any) -> Any:
        return _ai_effect_for(self)._ai_effect_task_scheduler(coro)

    def apply_ai_effect_preset(self, preset_id: str) -> str | None:
        if not ai_features_enabled():
            return None
        return _ai_effect_for(self).apply_ai_effect_preset(preset_id)

    async def _mcp_submit_generation(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return await _ai_effect_for(self)._mcp_submit_generation(arguments)

    async def _privacy_gated_generation_submitter(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Privacy fail-closed generation submitter exposed via HostAPI.

        The downstream gateway/service layer enforces the privacy gate as
        well; this wrapper fails early with an identifiable reason so plugin
        callers (SAM3 preview submit) surface it instead of a generic error.
        """
        from .mcp.safety import ensure_external_services_allowed  # noqa: PLC0415

        ensure_external_services_allowed(self.settings)
        return await self._mcp_submit_generation(arguments)

    def _mcp_cancel_generation(self, layer_id: str) -> bool:
        return _ai_effect_for(self)._mcp_cancel_generation(layer_id)

    @staticmethod
    def _autobind_active_layer_to_first_slot(canvas_service: Any, state: Any, preset: Any) -> None:
        AIEffectCoordinator._autobind_active_layer_to_first_slot(canvas_service, state, preset)

    async def _load_plugins(self) -> None:
        await _plugin_system_for(self)._load_plugins()

    async def _create_main_window(self) -> None:
        """Create and configure main window."""
        try:
            from .ui.main_window import MainWindow  # noqa: PLC0415

            if not self._host_api:
                raise RuntimeError("HostAPI not initialized")
            if not self.plugin_registry:
                raise RuntimeError("PluginRegistry not initialized")

            self.main_window = MainWindow(self._host_api, self.plugin_registry)
            self.main_window.set_plugin_registry(self.plugin_registry)
            self.main_window.set_host_api(self._host_api)
            app_window_ai_wiring.attach_generation_handlers(self)
            if self._ui_service is not None:
                self._ui_service.set_main_window(self.main_window)

            app_window_ai_wiring.register_manager_before_restore(self)
            await self.main_window.initialize_async()
            app_window_ai_wiring.connect_after_initialize(self)
            self.main_window.show()
            app_window_ai_wiring.schedule_local_runtime(self)

            # Defer recovery check until after event loop is running
            if hasattr(self, "_autosave_manager"):
                QTimer.singleShot(500, self._autosave_manager.check_recovery)

            logger.info("Main window created and shown")

        except Exception as e:
            missing = []
            if not self._host_api:
                missing.append("host_api")
            if not self.plugin_registry:
                missing.append("plugin_registry")

            if missing:
                logger.error(f"Main window creation failed - missing dependencies: {missing}")
            else:
                logger.error(f"Main window creation failed: {e}")

            if self.app:
                QMessageBox.critical(
                    None,
                    self._ts("Startup Error"),
                    self._ts("Failed to create main window: {error}").format(error=e),
                )
            raise

    def _on_recovery_available(self, recoveries: list[Any]) -> None:
        """Show recovery choices while retaining failed or in-use candidates."""
        from .ui.recovery_workflow import handle_recovery_dialog  # noqa: PLC0415

        handle_recovery_dialog(
            recoveries, manager=getattr(self, "_autosave_manager", None), window=self.main_window
        )

    async def cleanup(self) -> None:  # noqa: PLR0912
        """Clean up application resources."""
        logger.info("Starting application cleanup")

        if ai_features_enabled():
            from .plugins.comfyui.local_runtime import cancel_watch  # noqa: PLC0415

            await cancel_watch(self)

        try:
            if hasattr(self, "_autosave_manager"):
                self._autosave_manager.stop()

            if self.main_window and hasattr(self.main_window, "shutdown_mcp_runtime"):
                try:
                    await self.main_window.shutdown_mcp_runtime()
                except Exception as e:
                    logger.error(f"LoomStroke MCP runtime cleanup error: {e}")

            if self.main_window and hasattr(self.main_window, "async_cleanup"):
                try:
                    await self.main_window.async_cleanup()
                except Exception as e:
                    logger.error(f"Main window cleanup error: {e}")

            if self.plugin_registry and hasattr(self.plugin_registry, "cleanup"):
                try:
                    await self.plugin_registry.cleanup()
                except Exception as e:
                    logger.error(f"Plugin registry cleanup error: {e}")

            # AI Effect subsystem cleanup (Phase 6-a).
            if self._ai_effect_gateway_registry is not None:
                for gw_id in list(self._ai_effect_gateway_registry.list_registered()):
                    try:
                        gw = self._ai_effect_gateway_registry.get(gw_id)
                        await gw.dispose()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Gateway %s dispose failed: %s", gw_id, e)
            # ComfyUI connection lifecycle is owned by the ComfyUI plugin
            # service now (post-deduplication); no separate disconnect needed
            # from the AI Effect subsystem.

            if self.async_manager and hasattr(self.async_manager, "cleanup"):
                try:
                    await self.async_manager.cleanup()
                except Exception as e:
                    logger.error(f"Async manager cleanup error: {e}")

            if self.settings and hasattr(self.settings, "settings"):
                try:
                    self.settings.settings.sync()
                except Exception as e:
                    logger.error(f"Settings sync error: {e}")

            logger.info("Application cleanup complete")

        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    def run(self) -> int:
        """
        Run the application.

        Returns:
            Exit code
        """
        try:
            self.create_app()
            loop = self.create_event_loop()

            with loop:
                try:
                    loop.run_until_complete(self.initialize())
                    exit_code: int = loop.run_until_complete(self._run_forever())
                    return exit_code
                finally:
                    # aboutToQuit stops qasync before scheduled cleanup can
                    # finish. Run it explicitly on its original loop instead
                    # of its post-stop processEvents() drain.
                    loop.run_until_complete(self.cleanup())

        except Exception as e:
            logger.error(f"Application run failed: {e}")
            if self.app:
                QMessageBox.critical(
                    None,
                    self._ts("Fatal Error"),
                    self._ts("Application failed to run: {error}").format(error=e),
                )
            return 1

        finally:
            if ai_features_enabled():
                from .plugins.comfyui.local_runtime import stop_owned  # noqa: PLC0415

                # aboutToQuit can stop qasync before cleanup completes.
                stop_owned(self)

    async def _run_forever(self) -> int:
        """Run the event loop forever until quit."""
        if not self.app:
            return 1

        quit_future: asyncio.Future[int] = asyncio.get_event_loop().create_future()

        def on_quit() -> None:
            if not quit_future.done():
                quit_future.set_result(0)

        self.app.aboutToQuit.connect(on_quit)
        return await quit_future


_DEFAULT_AI_EFFECT_OUTPUT_APPLICATION = LoomStrokeApp._apply_ai_effect_outputs


def main() -> int:
    """
    Main entry point.

    Returns:
        Exit code
    """
    app = LoomStrokeApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
