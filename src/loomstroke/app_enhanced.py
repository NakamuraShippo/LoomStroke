"""
Enhanced main application with complete qasync integration.

Per Master.md PR#4 specifications, provides full Qt/asyncio
integration with proper lifecycle management.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import logging.handlers
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

try:
    from qasync import QEventLoop
except ImportError:
    logging.critical("qasync is required. Install with: pip install qasync")
    sys.exit(1)

from .core.event_bus_extensions import get_extended_event_bus
from .host_api import HostAPI
from .platform import get_settings_manager
from .platform.async_manager_enhanced import get_enhanced_async_manager
from .platform.qt_async_integration import get_qt_async_integration
from .plugins.sdk import PluginRegistry

logger = logging.getLogger(__name__)


class LoomStrokeAppEnhanced:
    """
    Enhanced application class with comprehensive async support.

    Provides advanced features:
    - Plugin system integration
    - Event bus coordination
    - Enhanced async management
    - Qt/asyncio lifecycle management
    """

    def __init__(self) -> None:
        """Initialize enhanced application."""
        # Enable crash logging FIRST (before anything else)
        self._crash_log_file: Any = None
        self._setup_crash_logging()

        self.app: QApplication | None = None
        self.main_window: Any = None
        self.event_loop: QEventLoop | None = None

        # Core components
        self.event_bus: Any = None
        self.async_manager: Any = None
        self.settings_manager: Any = None
        self.qt_integration: Any = None
        self.plugin_registry: PluginRegistry | None = None
        self.host_api: Any = None

        # Setup logging first
        self._setup_logging()

        logger.info("LoomStroke Enhanced Application initialized")

    def _setup_crash_logging(self) -> None:
        """Set up crash logging to capture segmentation faults."""
        try:
            from .platform.user_dirs import default_user_root  # noqa: PLC0415

            crash_log_dir = default_user_root() / "logs"
            crash_log_dir.mkdir(parents=True, exist_ok=True)
            log_path = str(crash_log_dir / "loomstroke_crash.log")
        except Exception:
            log_path = os.path.join(tempfile.gettempdir(), "loomstroke_crash.log")
        try:
            self._crash_log_file = open(log_path, "w")  # noqa: SIM115
            faulthandler.enable(self._crash_log_file)
        except Exception:  # pragma: no cover - best effort
            pass

    def _setup_logging(self) -> None:
        """Set up comprehensive logging system with file persistence."""
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        # Configure root logger
        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            handlers=[
                logging.StreamHandler(sys.stdout),
            ],
        )

        # Persist logs to user_data/logs/
        try:
            from .platform.user_dirs import default_user_root  # noqa: PLC0415

            log_dir = default_user_root() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / "loomstroke.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(log_format))
            file_handler.setLevel(logging.DEBUG)
            logging.getLogger().addHandler(file_handler)
        except Exception:  # pragma: no cover - best effort
            print("WARNING: Could not set up file logging")

        # Set library log levels to reduce noise
        logging.getLogger("qasync").setLevel(logging.WARNING)
        logging.getLogger("PySide6").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)

        # Install global exception hooks
        self._install_exception_hooks()

        logger.info("Logging system configured")

    @staticmethod
    def _install_exception_hooks() -> None:
        """Install global exception hooks so unhandled errors are always logged."""
        _exc_logger = logging.getLogger("loomstroke.unhandled")

        _original_excepthook = sys.excepthook

        def _excepthook(exc_type, exc_value, exc_tb):  # type: ignore[no-untyped-def]  # sys.excepthook signature
            _exc_logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
            _original_excepthook(exc_type, exc_value, exc_tb)

        sys.excepthook = _excepthook

        def _threading_excepthook(args):  # type: ignore[no-untyped-def]  # threading.excepthook signature
            _exc_logger.critical(
                "Unhandled exception in thread %s",
                args.thread.name if args.thread else "<unknown>",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = _threading_excepthook

    def create_qt_app(self) -> QApplication:
        """
        Create Qt application with optimal settings.

        Returns:
            Configured QApplication instance
        """
        if self.app is None:
            # High DPI support
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )

            # Create application
            self.app = QApplication(sys.argv)

            # Configure application metadata
            self.app.setApplicationName("LoomStroke")
            self.app.setApplicationVersion("0.1")
            self.app.setOrganizationName("LoomStroke")
            self.app.setOrganizationDomain("loomstroke.dev")

            # Set application icon if available
            icon_path = Path(__file__).resolve().parents[2] / "img" / "favicon.png"
            if icon_path.exists():
                from PySide6.QtGui import QIcon  # noqa: PLC0415

                self.app.setWindowIcon(QIcon(str(icon_path)))

            # Enable Qt features
            self.app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
            self.app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

            from .ui.theme import apply_loomstroke_theme  # noqa: PLC0415

            apply_loomstroke_theme(self.app, "dark")
            logger.info("Qt application created with enhanced settings")

        return self.app

    def create_event_loop(self) -> QEventLoop:
        """
        Create qasync event loop with proper integration.

        Returns:
            QEventLoop instance
        """
        if self.event_loop is None:
            self.event_loop = QEventLoop(self.app)
            asyncio.set_event_loop(self.event_loop)

            # Configure event loop
            self.event_loop.set_debug(False)  # Disable for production

            logger.info("Async event loop created and configured")

        return self.event_loop

    async def initialize_core_components(self) -> None:
        """Initialize core application components."""
        try:
            # §12-M-real-bug fix: _initialize_core_services is a coroutine and
            # must be awaited. Previously it was called without await, causing
            # the event bus/settings initialization to be silently skipped.
            await self._initialize_core_services()
            self._initialize_host_api()
            logger.info("Core components initialization complete")
        except Exception as e:
            logger.error(f"Core components initialization failed: {e}")
            raise

    async def _initialize_core_services(self) -> None:
        self.event_bus = get_extended_event_bus()
        logger.info("Event bus initialized")

        self.settings_manager = get_settings_manager()
        await self.settings_manager.async_initialize()
        logger.info("Settings manager initialized")
        self._apply_theme_setting()

        self.async_manager = get_enhanced_async_manager()
        await self.async_manager.async_initialize()
        logger.info("Enhanced async manager initialized")

        self.qt_integration = get_qt_async_integration()
        await self.qt_integration.initialize(self.event_loop)
        logger.info("Qt async integration initialized")

        self.plugin_registry = PluginRegistry()
        await self.plugin_registry.initialize()
        logger.info("Plugin registry initialized")

    def _apply_theme_setting(self) -> None:
        if self.app is None or self.settings_manager is None:
            return
        theme = self.settings_manager.get("ui/theme", "dark")
        from .ui.theme import apply_loomstroke_theme  # noqa: PLC0415

        apply_loomstroke_theme(self.app, theme if isinstance(theme, str) else "dark")

    def _initialize_host_api(self) -> None:
        if self.event_bus is None:
            raise RuntimeError("Event bus is not initialized")
        if self.settings_manager is None:
            raise RuntimeError("Settings manager is not initialized")
        if self.host_api is not None:
            raise RuntimeError("Host API is already initialized")

        self.host_api = self._create_host_api()
        color_state = self._load_color_state()
        self.host_api.attach_color_state(color_state)
        self.host_api.emit_color_state(source="bootstrap")

        # Generation history store
        from .services.generation_history_store import (  # noqa: PLC0415
            GenerationHistoryStore,
        )

        self._generation_history = GenerationHistoryStore()
        self._generation_history.load()
        self.host_api.attach_generation_history(self._generation_history)

        logger.info("Host API initialized")

    def _create_host_api(self) -> HostAPI:
        from PySide6.QtGui import QColor  # noqa: PLC0415

        from .core.canvas_service_simple import SimpleCanvasService  # noqa: PLC0415
        from .core.color_state import ColorDescriptor, ColorState  # noqa: F401, PLC0415
        from .core.document_service import DocumentServiceImpl  # noqa: PLC0415
        from .core.page_service import PageServiceImpl  # noqa: PLC0415
        from .core.services import (  # noqa: PLC0415
            MinimalHistoryService,
            MinimalInputService,
            MinimalSettingsService,
            MinimalUIService,
        )
        from .io.file_io import FileIO  # noqa: PLC0415
        from .io.layer_io import LayerIO  # noqa: PLC0415
        from .platform.shortcut_manager import (  # noqa: PLC0415
            ShortcutManager,
            ShortcutServiceAdapter,
        )

        canvas_service = SimpleCanvasService(
            width=1920,
            height=1080,
            background=QColor(255, 255, 255),
        )
        if hasattr(canvas_service, "set_event_bus"):
            canvas_service.set_event_bus(self.event_bus)

        input_service = MinimalInputService()
        settings_service = MinimalSettingsService(self.settings_manager)
        ui_service = MinimalUIService()
        # CT-05 H4d2: document-wide history memory trims run coalesced on
        # the Qt event loop's idle turn instead of synchronously.
        page_service = PageServiceImpl(
            canvas_service,
            self.event_bus,
            history_trim_scheduler=lambda callback: QTimer.singleShot(0, callback),
        )
        # CT-05 H5b: the router-backed history service unifies content +
        # book history behind the unchanged HistoryService contract, and
        # the notifier binding makes it the single HISTORY_STATE /
        # HISTORY_CHANGED publisher (canvas raw emission is rerouted).
        history_service = MinimalHistoryService(
            canvas_service,
            event_bus=self.event_bus,
            router=page_service._document_history_router,
            state_notifier=page_service._history_state_notifier,
            page_context_resolver=page_service._resolve_history_page_context,
        )
        page_service._bind_document_history_listener(
            history_service.notify_document_history_changed
        )
        file_io = FileIO(self.settings_manager)
        layer_io = LayerIO(file_io)
        document_service = DocumentServiceImpl(
            canvas=canvas_service,
            layer_io=layer_io,
            file_io=file_io,
            event_bus=self.event_bus,
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
        shortcut_manager = ShortcutManager(self.settings_manager)
        shortcut_service = ShortcutServiceAdapter(shortcut_manager)

        # Autosave manager
        from .io.autosave import AutosaveManager  # noqa: PLC0415

        self._autosave_manager = AutosaveManager(
            settings=self.settings_manager,
            document_service=document_service,
            canvas=cast("Any", canvas_service),
            event_bus=self.event_bus,
        )
        self._autosave_manager.recovery_available.connect(self._on_recovery_available)
        self._autosave_manager.start()

        return HostAPI(
            canvas=canvas_service,
            history=history_service,
            input=input_service,
            settings=settings_service,
            events=self.event_bus,
            document=document_service,
            pages=page_service,
            ui=ui_service,
            shortcuts=shortcut_service,
        )

    def _load_color_state(self) -> Any:
        from .core.color_state import ColorDescriptor, ColorState  # noqa: PLC0415

        color_state_data = self.host_api.settings.get("color/state", {})
        if isinstance(color_state_data, str):
            try:
                import json as _json  # noqa: PLC0415

                color_state_payload = _json.loads(color_state_data)
            except Exception:
                color_state_payload = {}
        else:
            color_state_payload = color_state_data

        try:
            if color_state_payload:
                return ColorState.from_dict(color_state_payload)
            return ColorState(current=ColorDescriptor((0.0, 0.0, 0.0, 1.0)))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to restore color state: %s", exc)
            return ColorState(current=ColorDescriptor((0.0, 0.0, 0.0, 1.0)))

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(
            lambda source: QCoreApplication.translate("LoomStroke", source),
            text,
        )

    async def create_main_window(self) -> None:
        """Create and initialize main window."""
        try:
            from .ui.main_window import MainWindow  # noqa: PLC0415

            # Create main window with all components
            self.main_window = MainWindow(
                host=self.host_api,
                registry=cast(PluginRegistry, self.plugin_registry),
            )

            # Set up integrations
            self.main_window.set_plugin_registry(self.plugin_registry)
            self.main_window.set_host_api(self.host_api)
            ui_service = getattr(self.host_api, "ui", None)
            set_main_window = getattr(ui_service, "set_main_window", None)
            if callable(set_main_window):
                set_main_window(self.main_window)

            # Initialize async components
            await self.main_window.initialize_async()

            # Connect window signals to app lifecycle
            self.main_window.window_closing.connect(self._on_window_closing)

            # Show window
            self.main_window.show()
            self.main_window.raise_()
            self.main_window.activateWindow()

            # Defer recovery check until after event loop is running
            if hasattr(self, "_autosave_manager"):
                QTimer.singleShot(500, self._autosave_manager.check_recovery)

            logger.info("Main window created and displayed")

        except Exception as e:
            logger.error(f"Main window creation failed: {e}")

            # Show error to user
            if self.app:
                QMessageBox.critical(
                    None,
                    self._ts("Startup Error"),
                    f"{self._ts('Failed to create main window:')}\n{e}",
                )
            raise

    def _on_recovery_available(self, recoveries: list[Any]) -> None:
        """Show recovery dialog when autosave files from a previous session are found."""
        from .io.autosave import RecoveryInfo  # noqa: PLC0415
        from .ui.dialogs.recovery_dialog import RecoveryDialog  # noqa: PLC0415

        valid = [r for r in recoveries if isinstance(r, RecoveryInfo)]
        if not valid:
            return

        parent = self.main_window if self.main_window else None
        dialog = RecoveryDialog(valid, parent=parent)
        if dialog.exec() == RecoveryDialog.DialogCode.Accepted:
            selected = dialog.selected_recovery
            if selected and hasattr(self, "_autosave_manager"):
                success = self._autosave_manager.recover(selected)
                if success:
                    marker = getattr(
                        self.main_window,
                        "mark_document_recovered_from_autosave",
                        None,
                    )
                    if callable(marker):
                        marker(
                            original_path=selected.original_path,
                            recovery_path=selected.file_path,
                        )
                    logger.info("Document recovered from autosave")
                else:
                    logger.warning("Recovery failed")
                self._autosave_manager.cleanup_all_old_sessions()
        elif hasattr(self, "_autosave_manager"):
            self._autosave_manager.cleanup_all_old_sessions()

    async def load_plugins(self) -> None:
        """Load and initialize plugins."""
        try:
            if not self.plugin_registry:
                logger.warning("Plugin registry not available")
                return
            loaded_count = 0
            for plugin_module in self._builtin_plugin_modules():
                if self._load_plugin_module(plugin_module):
                    loaded_count += 1
            for plugin_id in self.plugin_registry.load_installed_plugins():
                self._register_plugin_tools(plugin_id)
                loaded_count += 1
            self._activate_default_tool(loaded_count)
            logger.info(f"Plugin loading complete: {loaded_count} plugins loaded")

        except Exception as e:
            logger.error(f"Plugin loading failed: {e}")
            # Continue without plugins rather than crash

    def _builtin_plugin_modules(self) -> list[str]:
        # Use pen_tool.py which has the correct interface
        return [
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

    def _load_plugin_module(self, plugin_module: str) -> bool:
        try:
            # Use the synchronous load_from_module method
            plugin_id = self.plugin_registry.load_from_module(plugin_module)  # type: ignore[union-attr]  # runtime-guarded None access
            logger.info(f"Loaded plugin: {plugin_module} (ID: {plugin_id})")
            self._register_plugin_tools(plugin_id)
            return True
        except Exception as e:
            logger.warning(f"Failed to load plugin {plugin_module}: {e}")
            return False

    def _register_plugin_tools(self, plugin_id: str) -> None:
        if not self.main_window or not hasattr(self.main_window, "tool_manager"):
            return
        plugin = self.plugin_registry.get(plugin_id) if self.plugin_registry else None
        if not plugin:
            return
        tools_to_register = self._resolve_plugin_tools(plugin_id, plugin)
        manager = self.main_window.tool_manager
        for tool in tools_to_register:
            if not hasattr(tool, "descriptor"):
                continue
            try:
                descriptor = tool.descriptor()
                if hasattr(manager, "has_tool") and manager.has_tool(descriptor.id):
                    logger.debug("Tool %s already registered; skipping", descriptor.id)
                    continue
                manager.register_tool(tool)
                logger.info(f"Registered tool: {descriptor.id}")
            except Exception as e:
                tool_name = getattr(tool, "__class__", type(tool)).__name__
                logger.warning(f"Failed to register tool {tool_name} from {plugin_id}: {e}")

    def _resolve_plugin_tools(self, plugin_id: str, plugin: object) -> list[object]:
        get_tools = getattr(plugin, "get_tools", None)
        if not callable(get_tools):
            return [plugin]
        try:
            return list(get_tools())
        except Exception as e:
            logger.warning(f"Plugin {plugin_id}.get_tools failed: {e}")
            return []

    def _activate_default_tool(self, loaded_count: int) -> None:
        if (
            loaded_count <= 0
            or not self.main_window
            or not hasattr(self.main_window, "tool_manager")
        ):
            return
        try:
            self.main_window.tool_manager.activate_tool("basic_pen")
            logger.info("Activated default tool: basic_pen")
        except Exception as e:
            logger.warning(f"Failed to activate default tool: {e}")

    async def run_async(self) -> int:
        """
        Run the application asynchronously.

        Returns:
            Exit code
        """
        try:
            # Initialize all components
            await self.initialize_core_components()
            await self.create_main_window()
            await self.load_plugins()

            logger.info("Application startup complete - running event loop")

            # Create completion future
            quit_future = asyncio.get_event_loop().create_future()

            # Connect quit signal
            def on_quit() -> None:
                if not quit_future.done():
                    quit_future.set_result(0)

            cast(QApplication, self.app).aboutToQuit.connect(on_quit)

            # Run until quit
            return cast(int, await quit_future)

        except Exception as e:
            logger.error(f"Application run failed: {e}")

            if self.app:
                QMessageBox.critical(
                    None,
                    self._ts("Fatal Error"),
                    f"{self._ts('Application failed to run:')}\n{e}",
                )

            return 1

    def _on_window_closing(self) -> None:
        """Handle main window closing."""
        logger.info("Main window closing - initiating shutdown")

        # Create cleanup task
        if self.event_loop and not self.event_loop.is_closed():
            self.event_loop.create_task(self.cleanup())

    async def cleanup(self) -> None:
        """Clean up application resources."""
        logger.info("Starting application cleanup")

        if hasattr(self, "_autosave_manager"):
            self._autosave_manager.stop()

        # Cleanup main window
        try:
            if self.main_window and hasattr(self.main_window, "async_cleanup"):
                await self.main_window.async_cleanup()
                logger.debug("Main window cleanup complete")
        except Exception as e:
            logger.error(f"Main window cleanup error: {e}")

        # Cleanup host API
        try:
            if self.host_api and hasattr(self.host_api, "cleanup"):
                await self.host_api.cleanup()
                logger.debug("Host API cleanup complete")
        except Exception as e:
            logger.error(f"Host API cleanup error: {e}")

        # Cleanup plugin registry
        try:
            if self.plugin_registry:
                await self.plugin_registry.cleanup()
                logger.debug("Plugin registry cleanup complete")
        except Exception as e:
            logger.error(f"Plugin registry cleanup error: {e}")

        # Cleanup Qt integration
        try:
            if self.qt_integration and hasattr(self.qt_integration, "cleanup"):
                await self.qt_integration.cleanup()
                logger.debug("Qt integration cleanup complete")
        except Exception as e:
            logger.error(f"Qt integration cleanup error: {e}")

        # Cleanup async manager
        try:
            if self.async_manager:
                await self.async_manager.async_cleanup()
                logger.debug("Async manager cleanup complete")
        except Exception as e:
            logger.error(f"Async manager cleanup error: {e}")

        # Cleanup settings manager
        try:
            if self.settings_manager and hasattr(self.settings_manager, "cleanup"):
                await self.settings_manager.cleanup()
                logger.debug("Settings manager cleanup complete")
        except Exception as e:
            logger.error(f"Settings manager cleanup error: {e}")

        logger.info("Application cleanup complete")

    def run(self) -> int:
        """
        Run the enhanced application.

        Returns:
            Exit code
        """
        try:
            # Create Qt application and event loop
            app = self.create_qt_app()
            loop = self.create_event_loop()

            # Configure cleanup
            app.aboutToQuit.connect(lambda: loop.run_until_complete(self.cleanup()))

            # Run application
            with loop:
                return cast(int, loop.run_until_complete(self.run_async()))

        except KeyboardInterrupt:
            logger.info("Application interrupted by user")
            return 0

        except Exception as e:
            logger.error(f"Application failed: {e}")

            if self.app:
                QMessageBox.critical(
                    None,
                    self._ts("Fatal Error"),
                    f"{self._ts('Application crashed:')}\n{e}",
                )

            return 1


def main() -> int:
    """
    Enhanced main entry point.

    Returns:
        Exit code
    """
    app = LoomStrokeAppEnhanced()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
