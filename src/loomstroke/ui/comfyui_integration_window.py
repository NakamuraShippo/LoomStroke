"""
ComfyUI integration window.

Integrates all ComfyUI-related panels into a unified interface.
Per Master.md, this file is under 400 lines.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib import import_module
from typing import Any, cast

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.events import Events
from loomstroke.ui.translations import TranslationBinder

logger = logging.getLogger(__name__)


def _import_panel_class(module_path: str, class_name: str) -> type[Any]:
    """Load a panel class dynamically to avoid hard imports for mypy."""
    module = import_module(module_path)
    panel_cls = getattr(module, class_name)
    return cast(type[Any], panel_cls)


def _build_extended_event_bus() -> Any:
    """Return an extended event bus instance if available."""
    module = import_module("loomstroke.core.event_bus_extensions")
    return module.get_extended_event_bus()


def _panel_outline_color(widget: QWidget) -> str:
    palette = widget.palette()
    base = palette.color(QPalette.ColorRole.Window)
    line = palette.color(QPalette.ColorRole.Midlight)
    if abs(line.lightness() - base.lightness()) < 12:
        line = base.lighter(170) if base.lightness() < 128 else base.darker(125)
    return line.name()


class ComfyUIIntegrationWindow(QMainWindow):
    """Integrated window hosting all ComfyUI panels."""

    tool_selected = Signal(str)
    tool_property_changed = Signal(str, object)
    job_cancel_requested = Signal(str)
    settings_changed = Signal(str, object)

    def __init__(self, async_manager: Any, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.async_manager = async_manager
        self.settings = settings
        self._translation_binder = TranslationBinder(self.tr)
        self._event_bus = None
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []

        self.central_widget: QWidget
        self.main_splitter: QSplitter
        self.left_panel_tabs: QTabWidget
        self.right_panel_tabs: QTabWidget

        self.connection_panel: Any | None = None
        self.preview_panel: Any | None = None
        self.job_queue_panel: Any | None = None
        self.tool_panel: Any | None = None
        self.settings_panel: Any | None = None
        self.comfyui_connection: Any | None = None

        self._setup_window()
        self._create_panels()
        self._setup_layout()
        self._connect_signals()
        self._subscribe_language_events()

        logger.info("ComfyUI integration window initialized")

    # ------------------------------------------------------------------
    def _setup_window(self) -> None:
        """Set high-level window properties."""
        window_title = self._ts("ComfyUI Integration")
        self._translation_binder.register(self.setWindowTitle, window_title)
        self.setWindowTitle(window_title)
        self.resize(1400, 900)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks
        )

    def _create_panels(self) -> None:
        """Instantiate panels dynamically to keep dependencies loose."""
        try:
            connection_cls = _import_panel_class(
                "loomstroke.ui.panels.connection_panel", "ConnectionPanel"
            )
            self.connection_panel = connection_cls(self.async_manager, self.settings)

            preview_cls = _import_panel_class("loomstroke.ui.panels.preview_panel", "PreviewPanel")
            self.preview_panel = preview_cls(parent=self, event_bus=_build_extended_event_bus())

            tool_cls = _import_panel_class("loomstroke.ui.panels.tool_panel", "ToolPanel")
            try:
                self.tool_panel = tool_cls(parent=self)
            except Exception as exc:
                logger.warning("Could not create tool panel: %s", exc)
                self.tool_panel = None

            settings_cls = _import_panel_class(
                "loomstroke.ui.panels.settings_panel", "SettingsPanel"
            )
            self.settings_panel = settings_cls(settings_manager=self.settings)

            logger.debug("All panels created successfully")

        except Exception as exc:
            logger.error("Failed to create panels: %s", exc)
            QMessageBox.critical(
                self,
                self._ts("Panel Creation Error"),
                self._ts("Failed to create UI panels: {error}").format(error=exc),
            )

    def _setup_layout(self) -> None:
        """Wire the main splitter and tab containers."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        left_tabs = QTabWidget()
        left_tabs.setObjectName("panel_tabs_left")
        splitter.addWidget(left_tabs)

        right_tabs = QTabWidget()
        right_tabs.setObjectName("panel_tabs_right")
        splitter.addWidget(right_tabs)

        outline_color = _panel_outline_color(self)
        left_tabs.setStyleSheet(
            f"QTabWidget#panel_tabs_left::pane {{ border: 1px solid {outline_color}; }}"
        )
        right_tabs.setStyleSheet(
            f"QTabWidget#panel_tabs_right::pane {{ border: 1px solid {outline_color}; }}"
        )

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.central_widget = central_widget
        self.main_splitter = splitter
        self.left_panel_tabs = left_tabs
        self.right_panel_tabs = right_tabs

        self._add_panels_to_tabs()

    def _add_panels_to_tabs(self) -> None:
        """Register panels in their respective tab widgets."""
        if self.connection_panel is not None:
            self.left_panel_tabs.addTab(self.connection_panel, self._ts("Connection"))
        if self.tool_panel is not None:
            self.left_panel_tabs.addTab(self.tool_panel, self._ts("Tools"))
        if self.preview_panel is not None:
            self.right_panel_tabs.addTab(self.preview_panel, self._ts("Preview"))
        if self.settings_panel is not None:
            self.right_panel_tabs.addTab(self.settings_panel, self._ts("Settings"))

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(self.tr, text)

    def _subscribe_language_events(self) -> None:
        try:
            from loomstroke.core.event_bus_extensions import get_extended_event_bus

            event_bus = get_extended_event_bus()
            subscribe = getattr(event_bus, "on", None) or getattr(event_bus, "subscribe", None)
            if callable(subscribe):
                handler = self._on_language_changed
                self._event_handlers.append(handler)
                self._event_tokens.append(subscribe(Events.LANGUAGE_CHANGED, handler))
                self._event_bus = event_bus
        except Exception:
            logger.debug("Failed to subscribe ComfyUI integration window to language changes")

    def _on_language_changed(self, _payload: Any | None = None) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()

        # Update dynamic tab titles while preserving tab/widget positions.
        if self.connection_panel is not None:
            index = self.left_panel_tabs.indexOf(self.connection_panel)
            if index >= 0:
                self.left_panel_tabs.setTabText(index, self._ts("Connection"))

        if self.tool_panel is not None:
            index = self.left_panel_tabs.indexOf(self.tool_panel)
            if index >= 0:
                self.left_panel_tabs.setTabText(index, self._ts("Tools"))

        if self.preview_panel is not None:
            index = self.right_panel_tabs.indexOf(self.preview_panel)
            if index >= 0:
                self.right_panel_tabs.setTabText(index, self._ts("Preview"))

        if self.settings_panel is not None:
            index = self.right_panel_tabs.indexOf(self.settings_panel)
            if index >= 0:
                self.right_panel_tabs.setTabText(index, self._ts("Settings"))

    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        """Hook panel signals to window-level relays."""
        self._connect_panel_signal(self.tool_panel, "tool_selected", self._on_tool_selected)
        self._connect_panel_signal(
            self.tool_panel,
            "property_changed",
            self._on_tool_property_changed,
        )
        self._connect_panel_signal(self.settings_panel, "setting_changed", self._on_setting_changed)

    @staticmethod
    def _connect_panel_signal(
        panel: Any | None,
        signal_name: str,
        handler: Callable[..., Any],
    ) -> None:
        if panel is None:
            return
        signal = getattr(panel, signal_name, None)
        if signal is not None and hasattr(signal, "connect"):
            signal.connect(handler)

    # ------------------------------------------------------------------
    @Slot(str)
    def _on_tool_selected(self, tool_id: str) -> None:
        logger.debug("Tool selected: %s", tool_id)
        self.tool_selected.emit(tool_id)

    @Slot(str, object)
    def _on_tool_property_changed(self, name: str, value: object) -> None:
        logger.debug("Tool property changed: %s = %s", name, value)
        self.tool_property_changed.emit(name, value)

    @Slot(str)
    def _on_job_cancel_requested(self, job_id: str) -> None:
        if self.comfyui_connection is not None:
            logger.info("Cancelling job: %s", job_id)
        self.job_cancel_requested.emit(job_id)

    @Slot(str, object)
    def _on_setting_changed(self, key: str, value: object) -> None:
        logger.debug("Setting changed: %s = %s", key, value)
        self.settings_changed.emit(key, value)

    # ------------------------------------------------------------------
    def set_comfyui_connection(self, connection: Any) -> None:
        """Distribute the ComfyUI connection to child panels."""
        self.comfyui_connection = connection

        for panel in (self.connection_panel, self.preview_panel, self.job_queue_panel):
            setter = getattr(panel, "set_connection", None)
            if callable(setter):
                setter(connection)

        logger.info("ComfyUI connection set for integration window")

    def show_panel(self, panel_name: str) -> None:
        """Bring the requested panel tab to the front if present."""
        panel_name_lower = panel_name.lower()

        for idx in range(self.left_panel_tabs.count()):
            if self.left_panel_tabs.tabText(idx).lower() == panel_name_lower:
                self.left_panel_tabs.setCurrentIndex(idx)
                return

        for idx in range(self.right_panel_tabs.count()):
            if self.right_panel_tabs.tabText(idx).lower() == panel_name_lower:
                self.right_panel_tabs.setCurrentIndex(idx)
                return

        logger.warning("Panel '%s' not found", panel_name)

    def get_panel(self, panel_name: str) -> QWidget | None:
        """Return a panel widget by friendly name if available."""
        normalized = panel_name.lower()
        lookup: dict[str, Any | None] = {
            "connection": self.connection_panel,
            "preview": self.preview_panel,
            "queue": self.job_queue_panel,
            "tools": self.tool_panel,
            "settings": self.settings_panel,
        }
        panel = lookup.get(normalized)
        return panel if isinstance(panel, QWidget) else None

    async def async_initialize(self) -> None:
        """Propagate async initialization to cooperative panels."""
        for panel in (self.connection_panel, self.preview_panel):
            initializer = getattr(panel, "async_initialize", None)
            if callable(initializer):
                await initializer()

    async def async_cleanup(self) -> None:
        """Propagate async cleanup to cooperative panels."""
        for panel in (self.connection_panel, self.preview_panel):
            cleanup = getattr(panel, "async_cleanup", None)
            if callable(cleanup):
                await cleanup()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Persist splitter state and geometry before closing."""
        if hasattr(self.settings, "set"):
            try:
                splitter_array = self.main_splitter.saveState().toBase64().data()
                splitter_state = cast(bytes, splitter_array).decode("ascii")

                geometry_array = self.saveGeometry().toBase64().data()
                geometry_data = cast(bytes, geometry_array).decode("ascii")

                settings_set = getattr(self.settings, "set", None)
                if callable(settings_set):
                    settings_set("ui/comfyui_integration/splitter_state", splitter_state)
                    settings_set("ui/comfyui_integration/geometry", geometry_data)

            except Exception as exc:  # pragma: no cover - settings backend specific
                logger.warning("Failed to persist window layout: %s", exc)

        super().closeEvent(event)
        logger.info("ComfyUI integration window closed")
