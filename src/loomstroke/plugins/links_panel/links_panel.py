"""Read-mostly Links table + explicit actions (CT-18 §5.2). Nothing reloads automatically."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.linked_assets import ResourceConsumerIndex, ResourceStatus
from loomstroke.ui.resource_link_coordinator import ResourceLinkCoordinator
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["LinksPanel"]

_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.psd);;All files (*)"


def _ts(text: str) -> str:
    return translated_literal_domain("LinksPanel", text, use_fallback=True)


class LinksPanel(QWidget):
    COLUMNS = ("Status", "Name", "Kind", "Used by", "Modified", "Hash")

    def __init__(
        self,
        coordinator: ResourceLinkCoordinator,
        parent: QWidget | None = None,
        *,
        file_picker: Callable[[], str] | None = None,
        confirm: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = coordinator
        self._file_picker = file_picker or self._pick_file
        self._confirm = confirm or self._ask
        self._index = ResourceConsumerIndex()
        self._statuses: dict[str, ResourceStatus] = {}
        self._build()

    # -- build -------------------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.refresh_button = self._button(row, "Refresh", self.refresh)
        self.verify_button = self._button(row, "Verify Hashes", self.verify)
        self.reveal_button = self._button(row, "Reveal", self.reveal_selected)
        self.relink_button = self._button(row, "Relink...", self.relink_selected)
        self.reload_button = self._button(row, "Reload", self.reload_selected)
        self.replace_button = self._button(row, "Replace...", self.replace_selected)
        self.embed_button = self._button(row, "Embed", self.embed_selected)
        self.goto_button = self._button(row, "Go to Layer", self.go_to_selected)
        row.addStretch(1)
        layout.addLayout(row)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([_ts(c) for c in self.COLUMNS])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        layout.addWidget(self.table, 1)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._sync_buttons()

    def _button(self, row: QHBoxLayout, text: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(_ts(text))
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    # -- data --------------------------------------------------------------------------

    def refresh(self, *, verify: bool = False) -> None:
        self._index = self._coordinator.index()
        self._statuses = self._coordinator.statuses(self._index, refresh_hash=verify)
        self._fill()

    def verify(self) -> None:
        self.refresh(verify=True)

    def _fill(self) -> None:
        records = self._index.records()
        self.table.setRowCount(len(records))
        attention = 0
        for row, record in enumerate(records):
            status = self._statuses.get(record.resource_id)
            state = status.state if status is not None else "ok"
            attention += int(state != "ok")
            reason = status.reason_code if status is not None else ""
            badge = _ts(state) + (f" ({reason})" if reason and state != "ok" else "")
            consumers = self._index.consumers_for(record.resource_id)
            used = ", ".join(consumer.label for consumer in consumers[:3])
            if len(consumers) > 3:
                used += f" +{len(consumers) - 3}"
            modified = ""
            if status is not None and status.modified_at:
                modified = datetime.fromtimestamp(status.modified_at).strftime("%Y-%m-%d %H:%M")
            hash_state = self._hash_state(record.content_hash, status)
            mode = _ts("embedded") if record.mode == "embedded" else _ts("linked")
            cells = (
                badge,
                record.display_name,
                f"{record.kind} / {mode}",
                used,
                modified,
                hash_state,
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(0x0100, record.resource_id)  # Qt.UserRole
                self.table.setItem(row, column, item)
        self.status_label.setText(
            _ts("{count} resource(s), {attention} need attention").format(
                count=len(records), attention=attention
            )
        )
        self._sync_buttons()

    @staticmethod
    def _hash_state(recorded: str, status: ResourceStatus | None) -> str:
        if status is None or not status.current_hash:
            return _ts("not verified")
        return _ts("match") if status.current_hash == recorded else _ts("changed")

    # -- selection ---------------------------------------------------------------------

    def selected_resource_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        value = item.data(0x0100) if item is not None else None
        return str(value) if value else None

    def select_row(self, row: int) -> None:
        self.table.selectRow(row)

    def _sync_buttons(self) -> None:
        rid = self.selected_resource_id()
        record = self._index.record(rid) if rid else None
        linked = record is not None and record.is_linked
        for button in (
            self.reveal_button,
            self.relink_button,
            self.reload_button,
            self.replace_button,
            self.embed_button,
        ):
            button.setEnabled(linked)
        self.goto_button.setEnabled(record is not None)

    # -- actions -----------------------------------------------------------------------

    def reveal_selected(self) -> Path | None:
        rid = self.selected_resource_id()
        path = self._coordinator.reveal_path(rid) if rid else None
        if path is None:
            self._report(_ts("The file location is unknown."))
            return None
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        return path

    def reload_selected(self) -> None:
        rid = self.selected_resource_id()
        if not rid:
            return
        count, error = self._coordinator.reload(rid)
        self._after_mutation(count, error, _ts("Reloaded {count} layer(s)."))

    def relink_selected(self) -> None:
        self._relink(require_same_content=True)

    def replace_selected(self) -> None:
        self._relink(require_same_content=False)

    def _relink(self, *, require_same_content: bool) -> None:
        rid = self.selected_resource_id()
        if not rid:
            return
        chosen = self._file_picker()
        if not chosen:
            return
        plan = self._coordinator.plan_relink(rid, Path(chosen))
        if plan is None:
            return
        if require_same_content and not plan.hash_matches:
            question = _ts("{name} has different content than the linked resource. Relink anyway?")
            if not self._confirm(question.format(name=plan.source_path.name)):
                return
        count, error = self._coordinator.relink(plan)
        self._after_mutation(count, error, _ts("Relinked {count} layer(s)."))

    def embed_selected(self) -> None:
        rid = self.selected_resource_id()
        if not rid:
            return
        count, error = self._coordinator.embed(rid)
        self._after_mutation(count, error, _ts("Embedded {count} layer(s)."))

    def go_to_selected(self) -> None:
        rid = self.selected_resource_id()
        consumers = self._index.consumers_for(rid) if rid else ()
        if consumers:
            self._coordinator.go_to_layer(consumers[0])

    def _after_mutation(self, count: int, error: str, template: str) -> None:
        if error:
            self._report(_ts("Nothing changed: {code}").format(code=error))
        else:
            self._report(template.format(count=count))
        self.refresh()

    def _report(self, text: str) -> None:
        self.status_label.setText(text)

    def _pick_file(self) -> str:
        chosen, _filter = QFileDialog.getOpenFileName(self, _ts("Choose the file"), "", _FILTER)
        return chosen

    def _ask(self, question: str) -> bool:
        answer = QMessageBox.question(self, _ts("Links"), question)
        return answer == QMessageBox.StandardButton.Yes
