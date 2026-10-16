"""Links panel plugin registration (CT-18)."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QWidget

from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.document_path_locator import DocumentPathLocator
from loomstroke.ui.resource_link_coordinator import ResourceLinkCoordinator
from loomstroke.ui.translations import translated_literal_domain

from .links_panel import LinksPanel

logger = logging.getLogger(__name__)

__all__ = ["LinksPanelPlugin", "create_plugin"]


def _ts(text: str) -> str:
    return translated_literal_domain("LinksPanel", text, use_fallback=True)


class LinksPanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="links_panel",
        name=_ts("Links Panel"),
        version="0.1.0",
        description=_ts("Linked and embedded resources: status, relink, reload, embed"),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: LinksPanel | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="links_panel",
            display_name=_ts("Links"),
            category="tools",
            default_position="right",
            default_size=(640, 320),
            resizable=True,
        )

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]
        super().activate(host_api)
        self._host = host_api

    def deactivate(self) -> None:
        self._panel = None
        self._host = None
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._host is None:
            raise RuntimeError("Host API not set")
        if self._panel is None:
            coordinator = ResourceLinkCoordinator(
                self._host, document_dir_provider=DocumentPathLocator().document_dir
            )
            self._panel = LinksPanel(coordinator, parent=parent)
        return self._panel


def create_plugin() -> LinksPanelPlugin:
    return LinksPanelPlugin()
