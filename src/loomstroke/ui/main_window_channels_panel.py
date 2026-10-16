"""Expose embedded channels, with a dock fallback for placeholder layer panels."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget

from .panels.channels_panel import ChannelsPanel


class MainWindowChannelsPanelBootstrap:
    @staticmethod
    def setup(window: Any) -> None:
        tabs = getattr(getattr(window, "_layer_panel", None), "_channels_tabs", None)
        if tabs is not None:
            window._channels_panel = tabs.channels
            return
        dock = QDockWidget("Channels", window)
        panel = ChannelsPanel(window.host, getattr(window, "_event_bus", None), dock)
        dock.setWidget(panel)
        window._channels_panel = panel
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        window.register_panel(
            "ui.channels", "Channels", dock, default_visible=True, title_context="ChannelsPanel"
        )
        layer_dock = getattr(window, "_panel_docks", {}).get("ui.layers")
        if layer_dock is not None:
            window.tabifyDockWidget(layer_dock, dock)
            layer_dock.raise_()
