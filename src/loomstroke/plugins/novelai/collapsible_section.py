"""Remember the expanded state of a NovelAI settings section."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget

from loomstroke.platform.settings_manager import get_settings_manager

logger = logging.getLogger(__name__)


class CollapsibleSection(QWidget):
    def __init__(self, title: str, key: str, *, expanded: bool, parent=None, settings=None):
        super().__init__(parent)
        self._settings = settings if settings is not None else get_settings_manager()
        self._key = key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.header = QToolButton(self)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.header)
        self.body = QWidget(self)
        self.content_layout = QVBoxLayout(self.body)
        self.content_layout.setContentsMargins(4, 0, 0, 0)
        layout.addWidget(self.body)
        try:
            saved = self._settings.get(key, expanded)
        except Exception:
            logger.warning("Could not read NovelAI section state", exc_info=True)
            saved = expanded
        self.header.setChecked(saved if type(saved) is bool else expanded)
        self.body.setVisible(self.header.isChecked())
        self._update_arrow()
        self.set_title(title)
        self.header.toggled.connect(self._on_toggled)

    def set_title(self, title: str) -> None:
        self.header.setText(title)
        self.header.setAccessibleName(title)

    def _update_arrow(self) -> None:
        arrow = Qt.ArrowType.DownArrow if self.header.isChecked() else Qt.ArrowType.RightArrow
        self.header.setArrowType(arrow)

    def _on_toggled(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self._update_arrow()
        try:
            self._settings.set(self._key, expanded)
        except Exception:
            logger.warning("Could not save NovelAI section state", exc_info=True)
