"""Actions panel plugin registration (CT-20)."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QWidget

from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.action_service import ActionService
from loomstroke.ui.document_path_locator import DocumentPathLocator
from loomstroke.ui.translations import translated_literal_domain

from .actions_panel import ActionsPanel

logger = logging.getLogger(__name__)

__all__ = ["ActionsPanelPlugin", "create_plugin"]


def _ts(text: str) -> str:
    return translated_literal_domain("ActionsPanel", text, use_fallback=True)


class ActionsPanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="actions_panel",
        name=_ts("Actions Panel"),
        version="0.1.0",
        description=_ts("Record, play and batch declarative macros"),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: ActionsPanel | None = None
        self._service: ActionService | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="actions_panel",
            display_name=_ts("Actions"),
            category="tools",
            default_position="right",
            default_size=(420, 520),
            resizable=True,
        )

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]
        super().activate(host_api)
        self._host = host_api

    def deactivate(self) -> None:
        self._panel = None
        self._service = None
        self._host = None
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._host is None:
            raise RuntimeError("Host API not set")
        if self._service is None:
            self._service = ActionService(
                self._host, document_dir_provider=DocumentPathLocator().document_dir
            )
        if self._panel is None:
            self._panel = ActionsPanel(self._service, parent=parent)
        return self._panel


def create_plugin() -> ActionsPanelPlugin:
    return ActionsPanelPlugin()
