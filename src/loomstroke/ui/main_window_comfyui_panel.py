"""ComfyUI special panel lifecycle coordination for MainWindow."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QLabel

from loomstroke.ui.widget_translations import bind_widget_text

logger = logging.getLogger(__name__)


class MainWindowComfyUIPanelCoordinator:
    """Coordinate the special ``ui.comfyui`` dock lifecycle."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def initialize_state(self) -> None:
        window = self._window
        window._comfyui_panel_widget = None
        window._comfyui_dock = None
        window._active_comfyui_workflow = None

    def setup_if_available(self) -> None:
        window = self._window
        # The native ComfyUI frontend is hosted by AI Effects Manager. Keep
        # the legacy plugin/service active for its Gateway backend, but do not
        # construct the older standalone dock or its fallback placeholder.
        window._comfyui_panel_widget = None
        window._comfyui_dock = None
        window._active_comfyui_workflow = None
        logger.info("Legacy ComfyUI dock omitted; use AI Effects Manager > ComfyUI")

    def _create_plugin_panel(self, plugin: Any) -> None:
        window = self._window
        logger.info("ComfyUI plugin found, creating panel...")
        try:
            comfyui_widget = plugin.create_widget(window)
        except Exception as exc:
            logger.error(
                "ComfyUI plugin panel creation failed: %s",
                exc,
                exc_info=True,
            )
            logger.info("Attempting fallback ComfyUI panel creation...")
            self.create_fallback_panel()
            return

        comfyui_dock = QDockWidget("ComfyUI", window)
        window._comfyui_dock = comfyui_dock
        self.bind_panel(comfyui_widget)
        comfyui_dock.setWidget(comfyui_widget)

        window._apply_color_panel_min_width(comfyui_dock, fallback=400)
        comfyui_dock.resize(400, 600)

        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, comfyui_dock)
        window.register_panel(
            "ui.comfyui",
            "ComfyUI",
            comfyui_dock,
            default_visible=True,
            owner_plugin_id="comfyui_ui_panel",
        )

        if hasattr(plugin, "on_panel_shown"):
            plugin.on_panel_shown()
        logger.info("ComfyUI integrated panel added successfully")

    def create_fallback_panel(self) -> None:
        """Create ComfyUI panel using direct import as fallback."""
        window = self._window
        try:
            module = importlib.import_module("loomstroke.ui.panels.comfyui_panel")
            ComfyUIPanel = getattr(module, "ComfyUIPanel", None)
            if ComfyUIPanel is None:
                raise AttributeError("ComfyUIPanel symbol is missing")

            logger.info("Creating fallback ComfyUI panel...")

            comfyui_dock = QDockWidget("ComfyUI", window)
            window._comfyui_dock = comfyui_dock

            comfyui_widget = ComfyUIPanel(window._async_manager)
            self.bind_panel(comfyui_widget)
            comfyui_dock.setWidget(comfyui_widget)

            window._apply_color_panel_min_width(comfyui_dock, fallback=400)
            comfyui_dock.resize(400, 600)

            window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, comfyui_dock)
            window.register_panel(
                "ui.comfyui",
                "ComfyUI",
                comfyui_dock,
                default_visible=True,
                owner_plugin_id="comfyui_ui_panel",
            )

            logger.info("ComfyUI fallback panel created successfully")

        except (ImportError, AttributeError) as exc:
            logger.error("ComfyUI panel not available: %s", exc)
            self.create_placeholder()
        except Exception as exc:
            logger.error("Failed to create fallback ComfyUI panel: %s", exc)
            self.create_placeholder()

    def create_placeholder(self) -> None:
        """Create minimal ComfyUI placeholder panel."""
        window = self._window
        try:
            comfyui_dock = QDockWidget("ComfyUI", window)
            window._comfyui_dock = comfyui_dock
            placeholder = bind_widget_text(
                QLabel(),
                "ComfyUI Panel\n(Not Available)",
                domain="MainWindowComfyUIPanelCoordinator",
            )
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #666; padding: 20px;")

            comfyui_dock.setWidget(placeholder)
            window._apply_color_panel_min_width(comfyui_dock, fallback=300)

            window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, comfyui_dock)
            window.register_panel(
                "ui.comfyui",
                "ComfyUI",
                comfyui_dock,
                default_visible=True,
                owner_plugin_id="comfyui_ui_panel",
            )

            logger.info("ComfyUI placeholder panel created")

        except Exception as exc:
            logger.error("Failed to create ComfyUI placeholder: %s", exc)

    def update_dock_title(self, connected: bool | None = None) -> None:
        dock = self._window._comfyui_dock
        if dock is None:
            return
        base_title = "ComfyUI"
        if connected is None:
            dock.setWindowTitle(base_title)
            self.apply_dock_status_style(None)
            return
        status = "Connected" if connected else "Disconnected"
        dock.setWindowTitle(f"{base_title} ({status})")
        self.apply_dock_status_style(connected)

    def apply_dock_status_style(self, connected: bool | None) -> None:
        dock = self._window._comfyui_dock
        if dock is None:
            return

        if connected is True:
            state = "connected"
        elif connected is False:
            state = "disconnected"
        else:
            state = ""
        dock.setProperty("sr_comfyui_state", state)

        style_rules = [
            (
                'QDockWidget[sr_comfyui_state="connected"]::title {'
                "background-color: #2e7d32; color: white; }"
            ),
            (
                'QDockWidget[sr_comfyui_state="disconnected"]::title {'
                "background-color: #c62828; color: white; }"
            ),
        ]
        existing = dock.styleSheet().strip()
        for rule in style_rules:
            if rule in existing:
                continue
            existing = f"{existing}\n{rule}" if existing else rule
        dock.setStyleSheet(existing)
        dock.style().unpolish(dock)
        dock.style().polish(dock)
        dock.update()

    def on_connection_status_changed(self, connected: bool) -> None:
        self.update_dock_title(connected)

    def bind_panel(self, panel: Any) -> None:
        window = self._window
        window._comfyui_panel_widget = panel
        signal = getattr(panel, "workflow_changed", None)
        if signal is not None and hasattr(signal, "connect"):
            signal.connect(window._on_comfyui_workflow_changed)
        status_signal = getattr(panel, "connection_status_changed", None)
        if status_signal is not None and hasattr(status_signal, "connect"):
            status_signal.connect(window._on_comfyui_connection_status_changed)
            connected = False
            getter = getattr(panel, "is_connected", None)
            if callable(getter):
                try:
                    connected = bool(getter())
                except Exception as exc:  # pragma: no cover - defensive logging
                    logger.debug("ComfyUI panel connection getter raised: %s", exc)
            self.update_dock_title(connected)
        else:
            self.update_dock_title(False)

    def on_workflow_changed(self, _workflow: object) -> None:
        window = self._window
        label = self.get_workflow_label()
        if not label:
            return
        previous = window._active_comfyui_workflow
        if previous == label:
            return
        if previous and previous != label:
            self.save_active_layout()
        window._active_comfyui_workflow = label
        if not self.apply_layout(label):
            window._workflow_layouts.save_current_layout(name=label)

    def get_workflow_label(self) -> str | None:
        panel = getattr(self._window, "_comfyui_panel_widget", None)
        if panel is None:
            return None
        getter = getattr(panel, "current_workflow_label", None)
        if callable(getter):
            try:
                label = getter()
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.debug("ComfyUI panel label getter raised: %s", exc)
            else:
                if isinstance(label, str) and label.strip():
                    return label.strip()
        path_obj = getattr(panel, "current_workflow_path", None)
        if isinstance(path_obj, Path):
            return path_obj.stem
        return None

    def save_active_layout(self) -> None:
        window = self._window
        if window._workflow_layouts and window._active_comfyui_workflow:
            window._workflow_layouts.save_current_layout(name=window._active_comfyui_workflow)

    def apply_layout(self, label: str) -> bool:
        snapshot = self._window._workflow_layouts.load_layout(name=label)
        if snapshot is None:
            return False
        self._window._apply_layout_snapshot(snapshot, clear_missing=False)
        return True
