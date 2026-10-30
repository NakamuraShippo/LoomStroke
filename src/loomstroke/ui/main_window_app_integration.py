"""Application integration coordination for MainWindow."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import QMainWindow, QMessageBox, QWidget

from .credential_migration_notice import install_credential_migration_notice
from .main_window_tool_plugin_bootstrap import MainWindowToolPluginBootstrapCoordinator
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)


def _tool_plugin_bootstrap_for(window: Any) -> MainWindowToolPluginBootstrapCoordinator:
    coordinator = getattr(window, "_tool_plugin_bootstrap_coordinator", None)
    if not isinstance(coordinator, MainWindowToolPluginBootstrapCoordinator):
        coordinator = MainWindowToolPluginBootstrapCoordinator(window)
        window._tool_plugin_bootstrap_coordinator = coordinator
    return coordinator


class MainWindowAppIntegrationCoordinator:
    """Coordinate MainWindow's public app/HostAPI/registry integration surface."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def set_central_widget(self, widget: QWidget) -> None:
        QMainWindow.setCentralWidget(self._window, widget)
        logger.info("Central widget set")

    def set_plugin_registry(self, registry: Any) -> None:
        window = self._window
        window._plugin_registry = registry
        window._register_tools_from_registry(
            getattr(window, "tool_manager", None),
            window._plugin_registry,
        )
        window._sync_tool_properties_panel_tool_manager()

        build_menubar = getattr(window, "_build_menubar", None)
        if callable(build_menubar):
            build_menubar()

        window._sync_ui_panel_plugins()
        window._setup_additional_panels()

    def set_host_api(self, host: Any) -> None:
        window = self._window
        window._host_api = host
        if window._plugin_registry is not None:
            window._plugin_registry.set_host_api(host)

        build_menubar = getattr(window, "_build_menubar", None)
        if callable(build_menubar):
            build_menubar()

        _tool_plugin_bootstrap_for(window).attach_host_to_tool_panels(host)
        channels_panel = getattr(window, "_channels_panel", None)
        if channels_panel is not None:
            channels_panel.set_host_api(host)

        window._update_history_actions()
        host_events = getattr(host, "events", None)
        if host_events is not None:
            window._subscribe_layer_events(host_events)

    async def initialize_async(self) -> None:
        window = self._window
        try:
            await window._async_manager.async_initialize()
            install_credential_migration_notice(window)
            window.window_ready.emit()
            logger.info("Async initialization complete")
        except Exception as exc:
            logger.error("Async initialization failed: %s", exc)
            QMessageBox.critical(
                window,
                translate_with_fallback("MainWindow", "Initialization Error"),
                translate_with_fallback("MainWindow", "Failed to initialize: {error}").format(
                    error=exc
                ),
            )
