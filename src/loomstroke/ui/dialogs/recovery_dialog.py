"""Recovery dialog shown at startup when autosave files are found."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..translations import TranslationBinder, translate_with_fallback

if TYPE_CHECKING:
    from loomstroke.io.autosave import RecoveryInfo

logger = logging.getLogger(__name__)


class RecoveryDialog(QDialog):
    """Dialog for selecting an autosave file to recover."""

    def __init__(
        self,
        recoveries: list[RecoveryInfo],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._recoveries = recoveries
        self._selected: RecoveryInfo | None = None
        self._discard_requested = False
        self._translations = TranslationBinder(self._ts)
        self._build_ui()

    def _build_ui(self) -> None:
        self._translations.register(self.setWindowTitle, "LoomStroke — Recovery")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        label = QLabel()
        self._translations.register(
            label.setText,
            "Autosave files from the previous session were found.\nSelect a file to recover.",
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self._list = QListWidget()
        for info in self._recoveries:
            item = QListWidgetItem(self._recovery_text(info))
            item.setData(Qt.ItemDataRole.UserRole, info)
            self._list.addItem(item)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(self)
        self._btn_recover = buttons.addButton("", QDialogButtonBox.ButtonRole.AcceptRole)
        self._translations.register(self._btn_recover.setText, "Recover")
        self._btn_discard = buttons.addButton("", QDialogButtonBox.ButtonRole.DestructiveRole)
        self._translations.register(self._btn_discard.setText, "Discard and Start New")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # DestructiveRole doesn't auto-connect; wire manually
        self._btn_discard.clicked.connect(self._discard)

        layout.addWidget(buttons)

    @staticmethod
    def _ts(source: str) -> str:
        return translate_with_fallback("RecoveryDialog", source)

    def _recovery_text(self, info: RecoveryInfo) -> str:
        return self._ts("{timestamp}  —  {name}  (session: {session})").format(
            timestamp=info.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            name=info.original_path.name if info.original_path else self._ts("(unsaved)"),
            session=info.session_id,
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "_list"):
            self._translations.retranslate()
            for index in range(self._list.count()):
                item = self._list.item(index)
                item.setText(self._recovery_text(item.data(Qt.ItemDataRole.UserRole)))

    def accept(self) -> None:
        self._discard_requested = False
        current = self._list.currentItem()
        if current is not None:
            self._selected = current.data(Qt.ItemDataRole.UserRole)
        super().accept()

    @property
    def selected_recovery(self) -> RecoveryInfo | None:
        """Return the RecoveryInfo chosen by the user, or None if discarded."""
        return self._selected

    @property
    def discard_requested(self) -> bool:
        return self._discard_requested

    def _discard(self) -> None:
        self._discard_requested = True
        self.reject()
