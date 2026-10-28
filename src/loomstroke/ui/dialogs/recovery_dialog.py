"""Recovery dialog shown at startup when autosave files are found."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

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
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("LoomStroke — Recovery")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        label = QLabel(
            "前回のセッションから自動保存ファイルが見つかりました。\n"
            "復元するファイルを選択してください。"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self._list = QListWidget()
        for info in self._recoveries:
            ts = info.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            original = info.original_path.name if info.original_path else "(未保存)"
            text = f"{ts}  —  {original}  (session: {info.session_id})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, info)
            self._list.addItem(item)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(self)
        self._btn_recover = buttons.addButton("復元", QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_discard = buttons.addButton(
            "破棄して新規開始", QDialogButtonBox.ButtonRole.DestructiveRole
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # DestructiveRole doesn't auto-connect; wire manually
        self._btn_discard.clicked.connect(self._discard)

        layout.addWidget(buttons)

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
