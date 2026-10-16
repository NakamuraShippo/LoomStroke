from __future__ import annotations

import logging

from PySide6.QtWidgets import QWidget

from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin

from .ui.panel import ShapeManagerPanel

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("ShapeManagerPlugin", text, use_fallback=True)


class ShapeManagerPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="loomstroke.shape_manager",
        name=_ts("Shape Manager"),
        version="0.1.0",
        description=_ts("Manage manga shapes, balloons, and frames."),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: ShapeManagerPanel | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="loomstroke.shape_manager.panel",
            display_name=_ts("Shape Manager"),
            category="tools",
            default_position="right",
            default_size=(360, 520),
            resizable=True,
            closable=True,
            floating=False,
        )

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._panel is None:
            self._panel = ShapeManagerPanel(parent=parent)
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
            except Exception:  # pragma: no cover - defensive cleanup
                logger.debug("Failed to dispose ShapeManager panel", exc_info=True)
            self._panel = None
        self._host = None
        super().deactivate()


def create_plugin() -> ShapeManagerPlugin:
    return ShapeManagerPlugin()
