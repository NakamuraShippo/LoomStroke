"""Drawing Assist panel plugin registration (CT-14)."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.canvas.drawing_assist_controller import DrawingAssistController
from loomstroke.ui.canvas.guide_canvas_bridge import guide_canvas, update_guide_overlay
from loomstroke.ui.translations import translated_literal_domain

from .drawing_assist_panel import DrawingAssistPanel
from .panel_sync import DrawingAssistPanelSync

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
        self._sync: DrawingAssistPanelSync | None = None

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
        if self._controller is not None:
            self._controller.clear_stroke_feedback()
        if self._sync is not None:
            self._sync.close()
            self._sync = None
        canvas = guide_canvas(self._canvas_widget())
        if (
            canvas is not None
            and getattr(canvas, "_drawing_assist_controller", None) is self._controller
        ):
            canvas._drawing_assist_controller = None
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
            self._sync = DrawingAssistPanelSync(
                self._panel, controller, getattr(self._host, "events", None)
            )
        return self._panel

    def _controller_instance(self) -> DrawingAssistController:
        if self._controller is None:
            self._controller = DrawingAssistController(self._host, repaint=self._repaint_canvas)
        if not self._bind_canvas():
            # MainWindow constructs its docks before UIService receives the window.
            QTimer.singleShot(0, self._bind_canvas)
        return self._controller

    def _bind_canvas(self) -> bool:
        canvas = guide_canvas(self._canvas_widget())
        if canvas is None or self._controller is None:
            return False
        canvas._drawing_assist_controller = self._controller  # noqa: SLF001
        return True

    def _repaint_canvas(self) -> None:
        if self._bind_canvas():
            update_guide_overlay(self._canvas_widget())

    def _canvas_widget(self) -> Any | None:
        ui = getattr(self._host, "ui", None)
        main_window = getattr(ui, "get_main_window", lambda: None)() if ui else None
        return getattr(main_window, "canvas", None) if main_window is not None else None


def create_plugin() -> DrawingAssistPanelPlugin:
    return DrawingAssistPanelPlugin()
