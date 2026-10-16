"""Comic Project panel widget (CT-17 Phase A-C UI, spec 17 §5).

Left: chapter / page tree.  Centre: dialogue table with search.  Right:
the selected dialogue (speaker / text / review / page / direction) and
the actions — add chapter / page / dialogue, link the page entry to the
current document page, create a text object from the dialogue (Command
path, link committed only after success), next unplaced / needs review,
import / export scripts, check links.  All edits go through
:class:`ComicProjectService` (revision-checked atomic sidecar saves); a
stale snapshot is reloaded and reported, never overwritten.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.comic_project.comic_document_link_resolver import ComicDocumentLinkResolver
from loomstroke.core.comic_project.comic_page_entry import ComicPageEntry
from loomstroke.core.comic_project.comic_project_service import ComicProjectService
from loomstroke.core.comic_project.comic_project_snapshot import ComicProjectSnapshot
from loomstroke.core.comic_project.comic_script_exporter import ComicScriptExporter
from loomstroke.core.comic_project.comic_script_importer import ComicScriptImporter
from loomstroke.core.comic_project.dialogue_apply_planner import DialogueApplyPlanner
from loomstroke.core.comic_project.dialogue_entry import DialogueEntry
from loomstroke.core.comic_project.link_report import LinkReport
from loomstroke.core.comic_project.revision_conflict_error import RevisionConflictError
from loomstroke.ui.translations import translated_literal_domain

from .comic_project_tree_builder import ComicProjectTreeBuilder
from .dialogue_table_builder import DialogueTableBuilder
from .dialogue_text_submitter import DialogueTextSubmitter
from .document_text_reader import ComicDocumentTextReader

logger = logging.getLogger(__name__)

__all__ = ["ComicProjectPanel"]

Change = Callable[[Any, ComicProjectSnapshot], ComicProjectSnapshot]


def _ts(text: str) -> str:
    return translated_literal_domain("ComicProjectPanel", text, use_fallback=True)


class ComicProjectPanel(QWidget):
    def __init__(
        self,
        host_api: Any,
        service: ComicProjectService,
        *,
        document_id_provider: Callable[[], str | None] | None = None,
        submitter: DialogueTextSubmitter | None = None,
        text_reader: ComicDocumentTextReader | None = None,
        file_dialog: Callable[[], Any] | None = None,
        message_box: Callable[[], Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host_api
        self._service = service
        self._document_id = document_id_provider or (lambda: None)
        self._submitter = submitter or DialogueTextSubmitter(host_api)
        self._reader = text_reader or ComicDocumentTextReader(getattr(host_api, "pages", None))
        self._file_dialog = file_dialog or (lambda: QFileDialog)
        self._message_box = message_box or (lambda: QMessageBox)
        self._snapshot: ComicProjectSnapshot | None = None
        self._diff_ids: set[str] = set()
        self._selected_dialogue_id: str | None = None
        self._tree_builder = ComicProjectTreeBuilder(_ts)
        self._table_builder = DialogueTableBuilder(_ts)
        self._build_top()
        self._build_centre()
        self._build_detail()
        self._build_layout()
        self._connect()
        self.refresh_projects()

    # -- widgets -----------------------------------------------------------------

    def _build_top(self) -> None:
        self.project_combo = QComboBox(self)
        self.new_button = QPushButton(_ts("New..."), self)
        self.import_button = QPushButton(_ts("Import..."), self)
        self.export_button = QPushButton(_ts("Export..."), self)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)

    def _build_centre(self) -> None:
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(_ts("Search dialogue..."))
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels([_ts("Chapters / pages")])
        self.table = QTableWidget(0, 6, self)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

    def _build_detail(self) -> None:
        self.speaker_edit = QLineEdit(self)
        self.text_edit = QPlainTextEdit(self)
        self.review_combo = QComboBox(self)
        for state in DialogueEntry.REVIEW_STATES:
            self.review_combo.addItem(_ts(state), state)
        self.direction_combo = QComboBox(self)
        for value in ("vertical", "horizontal"):
            self.direction_combo.addItem(_ts(value), value)
        self.page_combo = QComboBox(self)
        self.apply_button = QPushButton(_ts("Apply to dialogue"), self)
        self.add_chapter_button = QPushButton(_ts("Add chapter"), self)
        self.add_page_button = QPushButton(_ts("Add page"), self)
        self.add_dialogue_button = QPushButton(_ts("Add dialogue"), self)
        self.delete_dialogue_button = QPushButton(_ts("Delete dialogue"), self)
        self.link_page_button = QPushButton(_ts("Link page to current"), self)
        self.create_text_button = QPushButton(_ts("Create text object"), self)
        self.next_unplaced_button = QPushButton(_ts("Next unplaced"), self)
        self.next_review_button = QPushButton(_ts("Next needs review"), self)
        self.check_links_button = QPushButton(_ts("Check links"), self)

    def _action_buttons(self) -> tuple[QPushButton, ...]:
        return (
            self.add_chapter_button,
            self.add_page_button,
            self.add_dialogue_button,
            self.delete_dialogue_button,
            self.link_page_button,
            self.create_text_button,
            self.next_unplaced_button,
            self.next_review_button,
            self.check_links_button,
        )

    def _build_layout(self) -> None:
        top = QHBoxLayout()
        top.addWidget(QLabel(_ts("Project:"), self))
        top.addWidget(self.project_combo, 1)
        for button in (self.new_button, self.import_button, self.export_button):
            top.addWidget(button)

        detail = QVBoxLayout()
        for label, widget in (
            ("Speaker:", self.speaker_edit),
            ("Text:", self.text_edit),
            ("Review:", self.review_combo),
            ("Direction:", self.direction_combo),
            ("Page:", self.page_combo),
        ):
            detail.addWidget(QLabel(_ts(label), self))
            detail.addWidget(widget, 1 if widget is self.text_edit else 0)
        detail.addWidget(self.apply_button)
        for button in self._action_buttons():
            detail.addWidget(button)
        detail.addStretch(1)
        detail_widget = QWidget(self)
        detail_widget.setLayout(detail)

        centre = QVBoxLayout()
        centre.addWidget(self.search_edit)
        centre.addWidget(self.table, 1)
        centre_widget = QWidget(self)
        centre_widget.setLayout(centre)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.tree)
        splitter.addWidget(centre_widget)
        splitter.addWidget(detail_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status_label)

    def _connect(self) -> None:
        self.project_combo.currentIndexChanged.connect(lambda _i: self._open_selected_project())
        self.new_button.clicked.connect(self.create_project)
        self.import_button.clicked.connect(self.import_script)
        self.export_button.clicked.connect(self.export_script)
        self.search_edit.textChanged.connect(lambda _t: self._refresh_table())
        self.tree.itemSelectionChanged.connect(self._refresh_table)
        self.tree.itemDoubleClicked.connect(lambda _item, _col: self.activate_selected_page())
        self.table.itemSelectionChanged.connect(self._load_selected_dialogue)
        self.apply_button.clicked.connect(self.apply_dialogue_edits)
        self.add_chapter_button.clicked.connect(self.add_chapter)
        self.add_page_button.clicked.connect(self.add_page)
        self.add_dialogue_button.clicked.connect(self.add_dialogue)
        self.delete_dialogue_button.clicked.connect(self.delete_dialogue)
        self.link_page_button.clicked.connect(self.link_page_to_current)
        self.create_text_button.clicked.connect(self.create_text_object)
        self.next_unplaced_button.clicked.connect(self.next_unplaced)
        self.next_review_button.clicked.connect(self.next_needs_review)
        self.check_links_button.clicked.connect(self.check_links)

    # -- state -------------------------------------------------------------------

    @property
    def snapshot(self) -> ComicProjectSnapshot | None:
        return self._snapshot

    def refresh_projects(self) -> None:
        current = self.project_combo.currentData()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem(_ts("(no project)"), None)
        for project_id, title in self._service.list_projects():
            self.project_combo.addItem(title, project_id)
        target = current or self._service.find_for_document(self._document_id())
        index = self.project_combo.findData(target) if target else 0
        self.project_combo.setCurrentIndex(max(0, index))
        self.project_combo.blockSignals(False)
        self._open_selected_project()

    def open_project(self, project_id: str | None) -> None:
        self._snapshot = self._service.open(project_id) if project_id else None
        self._diff_ids.clear()
        self._selected_dialogue_id = None
        self._refresh_all()

    def _open_selected_project(self) -> None:
        project_id = self.project_combo.currentData()
        self.open_project(str(project_id) if project_id else None)

    def _refresh_all(self) -> None:
        self._tree_builder.rebuild(self.tree, self._snapshot)
        self._refresh_page_combo()
        self._refresh_table()

    def _refresh_page_combo(self) -> None:
        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        self.page_combo.addItem(_ts("(unassigned)"), None)
        if self._snapshot is not None:
            for entry in self._snapshot.page_entries_in_order():
                chapter = self._snapshot.chapters.get(entry.chapter_id)
                title = chapter.title if chapter else "?"
                self.page_combo.addItem(f"{title} / p.{entry.page_number}", entry.entry_id)
        self.page_combo.blockSignals(False)

    def visible_dialogues(self) -> tuple[DialogueEntry, ...]:
        if self._snapshot is None:
            return ()
        dialogues = self._snapshot.search(self.search_edit.text())
        kind, ident = self._tree_builder.selection(self.tree)
        if kind == "page" and ident:
            dialogues = tuple(d for d in dialogues if d.entry_id == ident)
        elif kind == "chapter" and ident:
            entry_ids = {e.entry_id for e in self._snapshot.page_entries_for_chapter(ident)}
            dialogues = tuple(d for d in dialogues if d.entry_id in entry_ids)
        return dialogues

    def _refresh_table(self) -> None:
        self._table_builder.rebuild(
            self.table,
            self._snapshot,
            self.visible_dialogues(),
            diff_ids=self._diff_ids,
            select_id=self._selected_dialogue_id,
        )
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._snapshot is None:
            self.status_label.setText(_ts("Not linked to a Comic Project."))
            return
        counts = self._snapshot.counts()
        self.status_label.setText(
            _ts(
                "{chapters} chapter(s), {pages} page(s) ({linked} linked), {dialogues} line(s), "
                "{placed} placed, {review} need review."
            ).format(
                chapters=counts["chapters"],
                pages=counts["pages"],
                linked=counts["linked_pages"],
                dialogues=counts["dialogues"],
                placed=counts["placed"],
                review=counts["needs_review"],
            )
        )

    def selected_dialogue(self) -> DialogueEntry | None:
        if self._snapshot is None:
            return None
        dialogue_id = self._table_builder.selected_id(self.table) or self._selected_dialogue_id
        return self._snapshot.dialogues.get(dialogue_id or "")

    def _load_selected_dialogue(self) -> None:
        dialogue_id = self._table_builder.selected_id(self.table)
        if dialogue_id is None or self._snapshot is None:
            return
        dialogue = self._snapshot.dialogues.get(dialogue_id)
        if dialogue is None:
            return
        self._selected_dialogue_id = dialogue_id
        self.speaker_edit.setText(dialogue.speaker)
        self.text_edit.setPlainText(dialogue.text)
        self.review_combo.setCurrentIndex(max(0, self.review_combo.findData(dialogue.review_state)))
        self.direction_combo.setCurrentIndex(
            max(0, self.direction_combo.findData(dialogue.reading_direction))
        )
        self.page_combo.setCurrentIndex(max(0, self.page_combo.findData(dialogue.entry_id)))

    # -- edits -------------------------------------------------------------------

    def _apply(self, change: Change) -> bool:
        if self._snapshot is None:
            return False
        try:
            self._snapshot = self._service.apply(self._snapshot, change)
        except RevisionConflictError:
            self._snapshot = self._service.open(self._snapshot.project.project_id)
            self._message_box().warning(
                self,
                _ts("Comic Project"),
                _ts("The project was changed elsewhere; it has been reloaded."),
            )
            self._refresh_all()
            return False
        except Exception:
            logger.warning("comic project change failed", exc_info=True)
            self._message_box().warning(
                self, _ts("Comic Project"), _ts("The change could not be saved.")
            )
            return False
        self._refresh_all()
        return True

    def create_project(self, title: str | None = None) -> str | None:
        if title is None:
            title, ok = QInputDialog.getText(self, _ts("New Comic Project"), _ts("Title:"))
            if not ok or not title.strip():
                return None
        snapshot = self._service.create_project(title, document_id=self._document_id())
        self.refresh_projects()
        index = self.project_combo.findData(snapshot.project.project_id)
        self.project_combo.setCurrentIndex(max(0, index))
        return snapshot.project.project_id

    def add_chapter(self, title: str | None = None) -> str | None:
        if self._snapshot is None:
            return None
        if title is None:
            title = _ts("Chapter {number}").format(number=len(self._snapshot.chapters) + 1)
        result: dict[str, str] = {}

        def change(m: Any, s: ComicProjectSnapshot) -> ComicProjectSnapshot:
            updated, chapter_id = m.add_chapter(s, title)
            result["id"] = chapter_id
            return updated

        return result.get("id") if self._apply(change) else None

    def add_page(self) -> str | None:
        if self._snapshot is None:
            return None
        kind, ident = self._tree_builder.selection(self.tree)
        chapter_id = ident if kind == "chapter" else None
        if kind == "page" and ident:
            chapter_id = self._snapshot.page_entries[ident].chapter_id
        if chapter_id is None:
            chapters = self._snapshot.chapters_in_order()
            chapter_id = chapters[0].chapter_id if chapters else self.add_chapter()
            if chapter_id is None:
                return None
        result: dict[str, str] = {}

        def change(m: Any, s: ComicProjectSnapshot) -> ComicProjectSnapshot:
            updated, entry_id = m.add_page_entry(s, chapter_id)
            result["id"] = entry_id
            return updated

        return result.get("id") if self._apply(change) else None

    def add_dialogue(self) -> str | None:
        if self._snapshot is None:
            return None
        kind, ident = self._tree_builder.selection(self.tree)
        entry_id = ident if kind == "page" else None
        speaker = self.speaker_edit.text().strip()
        text = self.text_edit.toPlainText()
        direction = str(self.direction_combo.currentData() or "vertical")
        result: dict[str, str] = {}

        def change(m: Any, s: ComicProjectSnapshot) -> ComicProjectSnapshot:
            updated, dialogue_id = m.add_dialogue(
                s,
                speaker=speaker,
                text=text or _ts("(new line)"),
                entry_id=entry_id,
                reading_direction=direction,
            )
            result["id"] = dialogue_id
            return updated

        if not self._apply(change):
            return None
        self._selected_dialogue_id = result["id"]
        self._table_builder.select_dialogue(self.table, result["id"])
        return result["id"]

    def apply_dialogue_edits(self) -> bool:
        dialogue = self.selected_dialogue()
        if dialogue is None:
            return False
        entry_id = self.page_combo.currentData()
        speaker = self.speaker_edit.text()
        text = self.text_edit.toPlainText()
        review = str(self.review_combo.currentData() or "draft")
        direction = str(self.direction_combo.currentData() or dialogue.reading_direction)

        def change(m: Any, s: ComicProjectSnapshot) -> ComicProjectSnapshot:
            updated = m.update_dialogue(
                s,
                dialogue.dialogue_id,
                text=text,
                speaker=speaker,
                review_state=review,
                reading_direction=direction,
            )
            if (entry_id or None) != dialogue.entry_id:
                updated = m.assign_dialogue(
                    updated, dialogue.dialogue_id, entry_id=str(entry_id) if entry_id else None
                )
            return updated

        return self._apply(change)

    def delete_dialogue(self) -> bool:
        dialogue = self.selected_dialogue()
        if dialogue is None:
            return False
        self._selected_dialogue_id = None
        return self._apply(lambda m, s: m.remove_dialogue(s, dialogue.dialogue_id))

    # -- document bridge ---------------------------------------------------------

    def _pages(self) -> Any:
        return getattr(self._host, "pages", None)

    def _current_page_id(self) -> str | None:
        getter = getattr(self._pages(), "get_active_page_id", None)
        try:
            value = getter() if callable(getter) else None
        except Exception:
            value = None
        return str(value) if value else None

    def selected_page_entry(self) -> ComicPageEntry | None:
        if self._snapshot is None:
            return None
        kind, ident = self._tree_builder.selection(self.tree)
        if kind == "page" and ident:
            return self._snapshot.page_entries.get(ident)
        dialogue = self.selected_dialogue()
        if dialogue is not None and dialogue.entry_id:
            return self._snapshot.page_entries.get(dialogue.entry_id)
        return None

    def link_page_to_current(self) -> bool:
        entry = self.selected_page_entry()
        page_id = self._current_page_id()
        if entry is None or page_id is None:
            self.status_label.setText(_ts("Select a page entry and open the page to link."))
            return False
        document_id = self._document_id()
        return self._apply(
            lambda m, s: m.link_page(s, entry.entry_id, document_id=document_id, page_id=page_id)
        )

    def _switch_to_page(self, page_id: str) -> bool:
        if self._current_page_id() == page_id:
            return True
        set_active = getattr(self._pages(), "set_active_page", None)
        if not callable(set_active):
            return False
        try:
            set_active(page_id)
        except Exception:
            logger.debug("comic project: page switch failed", exc_info=True)
            return False
        return self._current_page_id() == page_id

    def activate_selected_page(self) -> bool:
        entry = self.selected_page_entry()
        if entry is None or not entry.page_id:
            return False
        return self._switch_to_page(entry.page_id)

    def create_text_object(self) -> str | None:
        dialogue = self.selected_dialogue()
        if dialogue is None or self._snapshot is None:
            return None
        entry = self._snapshot.page_entries.get(dialogue.entry_id or "")
        if entry is None or not entry.page_id:
            self.status_label.setText(_ts("Assign the line to a linked page first."))
            return None
        if not self._switch_to_page(entry.page_id):
            self.status_label.setText(_ts("The linked page could not be opened."))
            return None
        canvas = getattr(self._host, "canvas", None)
        fallback = (
            int(getattr(canvas, "_width", 1000) or 1000),
            int(getattr(canvas, "_height", 1500) or 1500),
        )
        plan = DialogueApplyPlanner().plan_create_text(
            dialogue,
            page_id=entry.page_id,
            page_size=self._reader.page_size(entry.page_id, fallback),
        )
        object_id = self._submitter.create(plan)
        if not object_id:
            self.status_label.setText(_ts("The text object could not be created."))
            return None
        # Link only after the canvas command succeeded (spec 17 §8).
        page_id = entry.page_id
        self._apply(
            lambda m, s: m.link_text_object(
                s, dialogue.dialogue_id, page_id=page_id, object_id=object_id
            )
        )
        return object_id

    def next_unplaced(self) -> str | None:
        return self._jump(lambda s: s.unplaced_dialogues())

    def next_needs_review(self) -> str | None:
        return self._jump(lambda s: s.dialogues_needing_review())

    def _jump(
        self, pick: Callable[[ComicProjectSnapshot], tuple[DialogueEntry, ...]]
    ) -> str | None:
        if self._snapshot is None:
            return None
        candidates = pick(self._snapshot)
        if not candidates:
            return None
        ids = [d.dialogue_id for d in candidates]
        current = self._selected_dialogue_id
        target = ids[(ids.index(current) + 1) % len(ids)] if current in ids else ids[0]
        self._selected_dialogue_id = target
        self.tree.setCurrentItem(None)
        self.search_edit.clear()
        self._refresh_table()
        self._table_builder.select_dialogue(self.table, target)
        self.activate_selected_page()
        return target

    def check_links(self) -> LinkReport | None:
        if self._snapshot is None:
            return None
        page_ids = self._reader.page_ids()
        linked = {e.page_id for e in self._snapshot.page_entries.values() if e.page_id}
        texts = self._reader.texts_by_page(sorted(linked & set(page_ids)))
        report = ComicDocumentLinkResolver().resolve(
            self._snapshot, page_ids=page_ids, text_by_object=texts
        )
        self._diff_ids = {diff[0] for diff in report.text_diffs}
        self._refresh_table()
        self.status_label.setText(
            _ts(
                "Links: {unresolved_pages} missing page(s), {unplaced} unplaced, "
                "{unresolved_text_refs} lost text object(s), {duplicates} duplicate(s), "
                "{text_diffs} text difference(s)."
            ).format(**report.counts())
        )
        return report

    # -- import / export -----------------------------------------------------------

    def import_script(self, path: str | None = None) -> int:
        if self._snapshot is None:
            return 0
        if path is None:
            path, _selected = self._file_dialog().getOpenFileName(
                self, _ts("Import Script"), "", _ts("Script files (*.csv *.json)")
            )
        if not path:
            return 0
        fmt = "json" if str(path).lower().endswith(".json") else "csv"
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            self._message_box().warning(
                self, _ts("Import Script"), _ts("The file could not be read.")
            )
            return 0
        importer = ComicScriptImporter(self._service.mutator)
        preview = importer.preview(text, fmt, snapshot=self._snapshot)
        if preview.blocked:
            self._message_box().warning(
                self,
                _ts("Import Script"),
                _ts("The file was rejected: {codes}").format(codes=", ".join(preview.issues)),
            )
            return 0
        answer = self._message_box().question(
            self,
            _ts("Import Script"),
            _ts(
                "Add {add} line(s), update {update}, unchanged {unchanged}, invalid {invalid}. "
                "Apply?"
            ).format(**preview.counts),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return 0
        applied = {"count": 0}

        def change(_m: Any, s: ComicProjectSnapshot) -> ComicProjectSnapshot:
            updated, count = importer.apply(s, preview)
            applied["count"] = count
            return updated

        return applied["count"] if self._apply(change) else 0

    def export_script(self, path: str | None = None) -> bool:
        if self._snapshot is None:
            return False
        if path is None:
            path, _selected = self._file_dialog().getSaveFileName(
                self, _ts("Export Script"), "", _ts("Script files (*.csv *.json)")
            )
        if not path:
            return False
        exporter = ComicScriptExporter()
        is_json = str(path).lower().endswith(".json")
        text = exporter.json(self._snapshot) if is_json else exporter.csv(self._snapshot)
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError:
            self._message_box().warning(
                self, _ts("Export Script"), _ts("The file could not be written.")
            )
            return False
        return True

    # -- i18n --------------------------------------------------------------------

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.tree.setHeaderLabels([_ts("Chapters / pages")])
            self._refresh_all()
