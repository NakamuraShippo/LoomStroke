"""Batch editor entry point for the existing dialogue table selection."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from .dialogue_batch import DialogueBatch
from .dialogue_batch_dialog import DialogueBatchDialog
from .panel_formatting import _ts


def edit_selected_dialogues(panel):
    keys = [
        panel.dialogue_table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
        for index in sorted(
            panel.dialogue_table.selectionModel().selectedRows(), key=lambda x: x.row()
        )
    ]
    if not keys:
        return
    try:
        batch = DialogueBatch(panel._tool, keys)
    except (ValueError, KeyError):
        QMessageBox.warning(
            panel,
            _ts("Edit selected dialogues"),
            _ts("Could not open the selected dialogues. Refresh the list and try again."),
        )
        return
    if DialogueBatchDialog(batch, panel).exec():
        panel.refresh_dialogue()
        panel.refresh_from_tool()
