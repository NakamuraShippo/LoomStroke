"""Drawing Assist panel plugin registration (CT-14)."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import QWidget

from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.canvas.drawing_assist_controller import DrawingAssistController
from loomstroke.ui.translations import translated_literal_domain

from .drawing_assist_panel import DrawingAssistPanel

logger = logging.getLogger(__name__)

__all__ = ["DrawingAssistPanelPlugin", "create_plugin"]


def _ts(text: str) -> str:
    return translated_literal_domain("DrawingAssistPanel", text, use_fallback=True)


class DrawingAssistPanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="drawing_assist_panel",
        name=_ts("Drawing Assist Panel"),
        version="0.1.0",
        description=_ts("Symmetry, parallel / radial / concentric rulers and perspective"),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: DrawingAssistPanel | None = None
        self._controller: DrawingAssistController | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="drawing_assist_panel",
            display_name=_ts("Drawing Assist"),
            category="tools",
            default_position="right",
            default_size=(360, 420),
            resizable=True,
        )

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]
        super().activate(host_api)
        self._host = host_api

    def deactivate(self) -> None:
        self._panel = None
        self._controller = None
        self._host = None
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._host is None:
            raise RuntimeError("Host API not set")
        controller = self._controller_instance()
        if self._panel is None:
            self._panel = DrawingAssistPanel(controller, parent=parent)
        return self._panel

    def _controller_instance(self) -> DrawingAssistController:
        if self._controller is None:
            canvas_widget = self._canvas_widget()
            repaint = getattr(canvas_widget, "update", None)
            self._controller = DrawingAssistController(
                self._host, repaint=repaint if callable(repaint) else None
            )
            if canvas_widget is not None:
                # The canvas widget paints the overlay and stroke tools find the
                # controller here (plugins/sdk/drawing_assist.py).
                canvas_widget._drawing_assist_controller = self._controller  # noqa: SLF001
        return self._controller

    def _canvas_widget(self) -> Any | None:
        ui = getattr(self._host, "ui", None)
        main_window = getattr(ui, "get_main_window", lambda: None)() if ui else None
        return getattr(main_window, "canvas", None) if main_window is not None else None


def create_plugin() -> DrawingAssistPanelPlugin:
    return DrawingAssistPanelPlugin()
