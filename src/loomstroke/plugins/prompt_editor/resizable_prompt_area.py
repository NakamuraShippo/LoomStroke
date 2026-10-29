"""Explicit, persistent vertical resizing of embedded prompt editors."""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout, QWidget

from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.ui.translations import translated_literal_domain

logger = logging.getLogger(__name__)
FLOW_HEIGHT_KEY = "ui/liveprompt/flow_height"


class _ResizeHandle(QFrame):
    def __init__(self, area):
        super().__init__(area)
        self.area = area
        self._origin = None
        self.setFixedHeight(10)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.retranslate()

    def retranslate(self):
        self.setAccessibleName(
            translated_literal_domain("PromptComposer", "Resize input area", use_fallback=True)
        )
        self.setToolTip(
            translated_literal_domain(
                "PromptComposer", "Drag to resize the input area.", use_fallback=True
            )
        )

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = (event.globalPosition().y(), self.area.height())
            self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._origin is not None:
            y, height = self._origin
            self.area.set_area_height(height + int(event.globalPosition().y() - y))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            self._origin = None
            self.releaseMouse()
            self.area.save_height()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ResizablePromptArea(QWidget):
    """Width follows its panel; a handle controls the remembered input height.

    Only explicit drag release writes the preference. Ordinary Qt layout,
    language, panel-width and restoration events cannot overwrite it.
    """

    def __init__(self, content, settings=None, *, key=FLOW_HEIGHT_KEY, default_height=170):
        super().__init__(content.parentWidget())
        self.settings = settings if settings is not None else get_settings_manager()
        self.key = key
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(content, 1)
        self.handle = _ResizeHandle(self)
        layout.addWidget(self.handle)
        try:
            height = self.settings.get(self.key, default_height)
        except Exception:
            logger.warning("Could not read LivePrompt input height", exc_info=True)
            height = default_height
        if type(height) is not int:
            height = default_height
        self.set_area_height(height)

    def set_area_height(self, height):
        self.setFixedHeight(max(80, min(4096, height)))

    def save_height(self):
        try:
            self.settings.set(self.key, self.height())
        except Exception:
            logger.warning("Could not save LivePrompt input height", exc_info=True)
