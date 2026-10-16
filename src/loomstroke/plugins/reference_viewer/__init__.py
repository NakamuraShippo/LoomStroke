"""Reference image viewer panel plugin."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import QWidget

from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("ReferenceViewerPlugin", text, use_fallback=True)


class ReferenceViewerPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="loomstroke.reference_viewer",
        name=_ts("Reference"),
        version="0.1.0",
        description=_ts("View reference images in a dockable panel."),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: Any | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="loomstroke.reference_viewer.panel",
            display_name=_ts("Reference"),
            category="tools",
            default_position="right",
            default_size=(300, 400),
            resizable=True,
            closable=True,
            floating=False,
        )

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        from .panel import ReferenceViewerPanel  # noqa: PLC0415

        if self._panel is None:
            self._panel = ReferenceViewerPanel(parent=parent)
        return self._panel

    def activate(self, host_api: HostAPI) -> None:
        super().activate(host_api)
        self._host = host_api

    def deactivate(self) -> None:
        if self._panel is not None:
            try:
                self._panel.deleteLater()
            except Exception:
                logger.debug("Failed to dispose Reference panel", exc_info=True)
            self._panel = None
        self._host = None
        super().deactivate()


def create_plugin() -> ReferenceViewerPlugin:
    return ReferenceViewerPlugin()
