"""Switch the layer panel's contents between layers and channels."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from loomstroke.ui.translations import translate_with_fallback

from .channels_panel import ChannelsPanel


class LayerChannelsTabs(QTabWidget):
    def __init__(self, host: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("layer_channels_tabs")
        self.setDocumentMode(True)
        self.layers = QWidget(self)
        self.layers_layout = QVBoxLayout(self.layers)
        self.layers_layout.setContentsMargins(0, 0, 0, 0)
        self.layers_layout.setSpacing(6)
        self.channels = ChannelsPanel(host, parent=self)
        self.addTab(self.layers, "")
        self.addTab(self.channels, "")
        self.refresh_translations()
        self.currentChanged.connect(self._refresh_current)

    def set_host_api(self, host: Any) -> None:
        self.channels.set_host_api(host)

    def refresh_translations(self) -> None:
        self.setTabText(0, translate_with_fallback("MainWindow", "Layers"))
        self.setTabText(1, translate_with_fallback("ChannelsPanel", "Channels"))

    def _refresh_current(self, index: int) -> None:
        if index == 1:
            self.channels.refresh()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "channels"):
            self.refresh_translations()
