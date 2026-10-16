from __future__ import annotations

import logging

from PySide6.QtWidgets import QWidget

from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin

from .ui.panel import TransformPanel

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("TransformPanelPlugin", text, use_fallback=True)


class TransformPanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="loomstroke.transform_panel",
        name=_ts("Transform"),
        version="0.1.0",
        description=_ts("Numeric position/size/rotation input for selected objects."),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: TransformPanel | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="loomstroke.transform_panel.panel",
            display_name=_ts("Transform"),
            category="tools",
            default_position="right",
            default_size=(300, 180),
            resizable=True,
            closable=True,
            floating=False,
        )

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._panel is None:
            self._panel = TransformPanel(parent=parent)
            if self._host is not None:
                self._panel.set_host(self._host)
        return self._panel

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]  # plugin API signature divergence
        super().activate(host_api)
        self._host = host_api
        if self._panel is not None:
            self._panel.set_host(host_api)

    def deactivate(self) -> None:
        if self._panel is not None:
            try:
                self._panel.detach_host()
                self._panel.deleteLater()
            except Exception:
                logger.debug("Failed to dispose Transform panel", exc_info=True)
            self._panel = None
        self._host = None
        super().deactivate()


def create_plugin() -> TransformPanelPlugin:
    return TransformPanelPlugin()
