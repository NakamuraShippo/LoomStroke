"""Prompt Editor compose header: title, unsaved mark, Save and a More menu."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_SAVE_STYLE = (
    "QPushButton#promptSaveButton { background: #8b5cf6; color: #ffffff; border: 0;"
    " border-radius: 6px; padding: 4px 14px; font-weight: 700; }"
    "QPushButton#promptSaveButton:disabled { background: rgba(139, 92, 246, 0.35);"
    " color: rgba(255, 255, 255, 0.6); }"
)
_UNSAVED_STYLE = "color: #f0c674;"


class ComposeHeader(QWidget):
    """Owns the title/tags fields and save actions shown above the live prompt.

    The panel keeps its existing handlers and passes its translated ``texts`` so the
    strings stay in the PromptEditorPanel catalog; this widget only arranges controls.
    ``save_as_action`` and ``new_action`` replace the former icon buttons and
    keep their ``setEnabled`` contract.
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        texts: dict[str, str],
        register: Callable[[Callable[[str], None], str], None],
        on_save: Callable[[], None],
        on_save_as: Callable[[], None],
        on_new: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.title_edit = QLineEdit(self)
        register(self.title_edit.setPlaceholderText, texts["title"])
        row.addWidget(self.title_edit, 1)

        self.unsaved_label = QLabel(self)
        self.unsaved_label.setStyleSheet(_UNSAVED_STYLE)
        register(lambda text: self.unsaved_label.setText("\u25cf " + text), texts["unsaved"])
        self.unsaved_label.hide()
        row.addWidget(self.unsaved_label)

        self.save_button = QPushButton(self)
        self.save_button.setObjectName("promptSaveButton")
        self.save_button.setStyleSheet(_SAVE_STYLE)
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        register(self.save_button.setText, texts["save"])
        self.save_button.clicked.connect(on_save)
        row.addWidget(self.save_button)

        self.more_button = QToolButton(self)
        self.more_button.setText("\u2026")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        register(self.more_button.setToolTip, texts["more"])
        register(self.more_button.setAccessibleName, texts["more"])
        menu = QMenu(self.more_button)
        self.save_as_action = QAction(menu)
        register(self.save_as_action.setText, texts["save_as"])
        self.save_as_action.triggered.connect(on_save_as)
        self.new_action = QAction(menu)
        register(self.new_action.setText, texts["new"])
        self.new_action.triggered.connect(on_new)
        menu.addAction(self.save_as_action)
        menu.addAction(self.new_action)
        self.more_button.setMenu(menu)
        row.addWidget(self.more_button)
        layout.addLayout(row)

        self.tags_edit = QLineEdit(self)
        register(self.tags_edit.setPlaceholderText, texts["tags"])
        layout.addWidget(self.tags_edit)

    def set_dirty(self, dirty: bool) -> None:
        self.unsaved_label.setVisible(dirty)
