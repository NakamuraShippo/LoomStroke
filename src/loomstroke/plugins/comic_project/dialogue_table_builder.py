"""Dialogue table rows for the Comic Project panel (CT-17 §5.2 centre pane)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from loomstroke.core.comic_project.comic_project_snapshot import ComicProjectSnapshot
from loomstroke.core.comic_project.dialogue_entry import DialogueEntry

__all__ = ["DialogueTableBuilder"]

ID_ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ("#", "Speaker", "Text", "Page", "Review", "Placed")


class DialogueTableBuilder:
    def __init__(self, translate: Callable[[str], str]) -> None:
        self._tr = translate

    def headers(self) -> list[str]:
        return [self._tr(column) for column in COLUMNS]

    def rebuild(
        self,
        table: QTableWidget,
        snapshot: ComicProjectSnapshot | None,
        dialogues: Sequence[DialogueEntry],
        *,
        diff_ids: set[str] | None = None,
        select_id: str | None = None,
    ) -> None:
        selected = select_id or self.selected_id(table)
        table.setRowCount(0)
        table.setColumnCount(len(COLUMNS))
        table.setHorizontalHeaderLabels(self.headers())
        if snapshot is None:
            return
        table.setRowCount(len(dialogues))
        select_row = -1
        for row, dialogue in enumerate(dialogues):
            entry = snapshot.page_entries.get(dialogue.entry_id or "")
            page_label = f"p.{entry.page_number}" if entry else "-"
            placed = self._tr("yes") if dialogue.is_placed else self._tr("no")
            if diff_ids and dialogue.dialogue_id in diff_ids:
                placed += " ≠"
            one_line = " ".join(dialogue.text.split())
            cells = (
                str(row + 1),
                dialogue.speaker,
                one_line,
                page_label,
                self._tr(dialogue.review_state),
                placed,
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(ID_ROLE, dialogue.dialogue_id)
                table.setItem(row, column, item)
            if dialogue.dialogue_id == selected:
                select_row = row
        if select_row >= 0:
            table.selectRow(select_row)
        table.resizeColumnsToContents()

    @staticmethod
    def selected_id(table: QTableWidget) -> str | None:
        row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0)
        value = item.data(ID_ROLE) if item is not None else None
        return str(value) if value else None

    @staticmethod
    def select_dialogue(table: QTableWidget, dialogue_id: str) -> bool:
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.data(ID_ROLE) == dialogue_id:
                table.selectRow(row)
                table.scrollToItem(item)
                return True
        return False
