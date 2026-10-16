"""Chapter / page tree items for the Comic Project panel (CT-17 §5.2 left pane)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from loomstroke.core.comic_project.comic_project_snapshot import ComicProjectSnapshot

__all__ = ["ComicProjectTreeBuilder"]

KIND_ROLE = Qt.ItemDataRole.UserRole
ID_ROLE = Qt.ItemDataRole.UserRole + 1


class ComicProjectTreeBuilder:
    def __init__(self, translate: Callable[[str], str]) -> None:
        self._tr = translate

    def rebuild(self, tree: QTreeWidget, snapshot: ComicProjectSnapshot | None) -> None:
        selected_kind, selected_id = self.selection(tree)
        tree.clear()
        if snapshot is None:
            return
        to_select: QTreeWidgetItem | None = None
        for chapter in snapshot.chapters_in_order():
            entries = snapshot.page_entries_for_chapter(chapter.chapter_id)
            chapter_item = QTreeWidgetItem(
                [
                    self._tr("{title} ({count} pages)").format(
                        title=chapter.title, count=len(entries)
                    )
                ]
            )
            chapter_item.setData(0, KIND_ROLE, "chapter")
            chapter_item.setData(0, ID_ROLE, chapter.chapter_id)
            tree.addTopLevelItem(chapter_item)
            if (selected_kind, selected_id) == ("chapter", chapter.chapter_id):
                to_select = chapter_item
            for entry in entries:
                dialogues = snapshot.dialogues_for_entry(entry.entry_id)
                placed = sum(1 for d in dialogues if d.is_placed)
                label = self._tr("p.{number}  [{status}]  {placed}/{total} placed").format(
                    number=entry.page_number,
                    status=self._tr(entry.status),
                    placed=placed,
                    total=len(dialogues),
                )
                if entry.is_linked:
                    label += "  " + self._tr("(linked)")
                page_item = QTreeWidgetItem([label])
                page_item.setData(0, KIND_ROLE, "page")
                page_item.setData(0, ID_ROLE, entry.entry_id)
                chapter_item.addChild(page_item)
                if (selected_kind, selected_id) == ("page", entry.entry_id):
                    to_select = page_item
            chapter_item.setExpanded(True)
        if to_select is not None:
            tree.setCurrentItem(to_select)

    @staticmethod
    def selection(tree: QTreeWidget) -> tuple[str | None, str | None]:
        item = tree.currentItem()
        if item is None:
            return None, None
        kind = item.data(0, KIND_ROLE)
        ident = item.data(0, ID_ROLE)
        return (str(kind) if kind else None, str(ident) if ident else None)
