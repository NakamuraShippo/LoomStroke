"""Manage Masters dialog (CT-07 T3b, spec 07 §6.3).

Capture-based master editing (2026-08-03, user approved): masters are
created and updated by capturing the current page's vector layers — no
dedicated master editor.  Thin view over the duck-typed service extras
(``set_masters_namespace`` / ``apply_master_to_pages`` /
``unlink_master_instance``); every write is undoable through the book
history.  An update is two history entries (definition, then instances).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.batch_export_planner import resolve_scope_page_ids
from loomstroke.core.master_page_apply import (
    capture_master_layers_from_page,
    find_master_instance,
)
from loomstroke.core.master_page_model import (
    MasterDefinition,
    masters_namespace_payload,
    read_master_definitions,
)
from loomstroke.ui.translations import translated_literal_domain


def _ts(text: str) -> str:
    return translated_literal_domain("PagePanelPlugin", text, use_fallback=True)


class ManageMastersDialog(QDialog):
    """List, capture, apply, update, unlink, and delete document masters."""

    def __init__(
        self,
        *,
        page_service: Any,
        document_service: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._page_service = page_service
        self._document_service = document_service
        self.setWindowTitle(_ts("Manage Masters"))
        self.resize(460, 380)

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget(self)
        self.list_widget.itemSelectionChanged.connect(self._refresh_buttons)
        layout.addWidget(self.list_widget, 1)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        self.create_button = QPushButton(_ts("Create from Current Page..."), self)
        self.create_button.clicked.connect(self._on_create)
        self.apply_button = QPushButton(_ts("Apply"), self)
        self.apply_button.setMenu(self._build_apply_menu())
        self.update_button = QPushButton(_ts("Update from Current Page"), self)
        self.update_button.clicked.connect(self._on_update)
        self.unlink_button = QPushButton(_ts("Unlink on Current Page"), self)
        self.unlink_button.clicked.connect(self._on_unlink)
        self.delete_button = QPushButton(_ts("Delete"), self)
        self.delete_button.clicked.connect(self._on_delete)
        for button in (
            self.create_button,
            self.apply_button,
            self.update_button,
            self.unlink_button,
            self.delete_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        selected = self._selected_id()
        self.list_widget.clear()
        for master in self._masters():
            instance_count = len(self._pages_with_instance(master.id))
            item = QListWidgetItem(
                _ts("{name} (rev {revision}, {count} page(s))").format(
                    name=master.name, revision=master.revision, count=instance_count
                ),
                self.list_widget,
            )
            item.setData(Qt.ItemDataRole.UserRole, master.id)
            self.list_widget.addItem(item)
            if master.id == selected:
                self.list_widget.setCurrentItem(item)
        self._refresh_buttons()

    def _masters(self) -> tuple[MasterDefinition, ...]:
        read = getattr(self._document_service, "read_masters_metadata", None)
        if not callable(read):
            return ()
        try:
            return read_master_definitions({"plugins": {"loomstroke.comictools.masters": read()}})
        except Exception:
            return ()

    def _selected_id(self) -> str | None:
        item = self.list_widget.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, str) else None

    def _selected_master(self) -> MasterDefinition | None:
        master_id = self._selected_id()
        if master_id is None:
            return None
        return next((m for m in self._masters() if m.id == master_id), None)

    def _refresh_buttons(self) -> None:
        has_selection = self._selected_id() is not None
        for button in (
            self.apply_button,
            self.update_button,
            self.unlink_button,
            self.delete_button,
        ):
            button.setEnabled(has_selection)

    # ------------------------------------------------------------------
    def _page_ids(self) -> list[str]:
        list_pages = getattr(self._page_service, "list_pages", None)
        if not callable(list_pages):
            return []
        return [summary.id for summary in list_pages() or []]

    def _pages_with_instance(self, master_id: str) -> list[str]:
        snapshot = getattr(self._page_service, "get_page_snapshot", None)
        if not callable(snapshot):
            return []
        found: list[str] = []
        for page_id in self._page_ids():
            page = snapshot(page_id)
            if page is not None and find_master_instance(page.layers, master_id) is not None:
                found.append(page_id)
        return found

    def _current_page_snapshot(self) -> Any | None:
        persist = getattr(self._page_service, "persist_active_page", None)
        if callable(persist):
            persist()
        page_id = self._page_service.get_active_page_id()
        snapshot = getattr(self._page_service, "get_page_snapshot", None)
        return snapshot(page_id) if callable(snapshot) else None

    def _write_masters(self, masters: list[MasterDefinition]) -> bool:
        setter = getattr(self._page_service, "set_masters_namespace", None)
        if not callable(setter):
            return False
        return bool(setter(masters_namespace_payload(masters)))

    # ------------------------------------------------------------------
    def _on_create(self) -> None:
        page = self._current_page_snapshot()
        layers = capture_master_layers_from_page(page) if page is not None else ()
        if not layers:
            QMessageBox.information(
                self,
                _ts("Manage Masters"),
                _ts("The current page has no vector layers to capture."),
            )
            return
        name, ok = QInputDialog.getText(self, _ts("Manage Masters"), _ts("Master name:"))
        if not ok or not name.strip():
            return
        master = MasterDefinition(
            id=f"master.{uuid4().hex[:12]}",
            name=name.strip(),
            revision=1,
            layers=layers,
        )
        if self._write_masters([*self._masters(), master]):
            self.refresh()

    def _on_update(self) -> None:
        master = self._selected_master()
        if master is None:
            return
        page = self._current_page_snapshot()
        layers = capture_master_layers_from_page(page) if page is not None else ()
        if not layers:
            QMessageBox.information(
                self,
                _ts("Manage Masters"),
                _ts("The current page has no vector layers to capture."),
            )
            return
        targets = self._pages_with_instance(master.id)
        updated = MasterDefinition(
            id=master.id,
            name=master.name,
            revision=master.revision + 1,
            target_roles=master.target_roles,
            layers=layers,
        )
        answer = QMessageBox.question(
            self,
            _ts("Manage Masters"),
            _ts("Update this master and refresh {count} page(s)?").format(count=len(targets)),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        masters = [updated if m.id == master.id else m for m in self._masters()]
        if not self._write_masters(masters):
            return
        if targets:
            apply = getattr(self._page_service, "apply_master_to_pages", None)
            if callable(apply):
                apply(self._master_payload(updated), targets)
        self.refresh()

    def _build_apply_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(_ts("All Pages"), lambda: self._on_apply("all"))
        menu.addAction(_ts("Current Page"), lambda: self._on_apply("current"))
        menu.addAction(_ts("Target Roles"), lambda: self._on_apply("role"))
        return menu

    def _on_apply(self, scope: str) -> None:
        master = self._selected_master()
        if master is None:
            return
        reader = getattr(self._page_service, "get_page_metadata", None)
        snapshot = getattr(self._page_service, "get_page_snapshot", None)
        metadata_by_id = {
            page_id: (
                reader(page_id)
                if callable(reader)
                else (getattr(snapshot(page_id), "metadata", None) if callable(snapshot) else None)
            )
            for page_id in self._page_ids()
        }
        include_roles = None
        if scope == "role":
            include_roles = list(master.target_roles) or ["body"]
        page_ids = resolve_scope_page_ids(
            self._page_ids(),
            scope=scope,  # type: ignore[arg-type]
            current_page_id=self._page_service.get_active_page_id(),
            include_roles=include_roles,
            page_metadata_by_id=metadata_by_id,
        )
        if not page_ids:
            QMessageBox.information(self, _ts("Manage Masters"), _ts("No pages match this scope."))
            return
        apply = getattr(self._page_service, "apply_master_to_pages", None)
        if callable(apply) and apply(self._master_payload(master), list(page_ids)):
            self.refresh()

    def _on_unlink(self) -> None:
        master_id = self._selected_id()
        if master_id is None:
            return
        unlink = getattr(self._page_service, "unlink_master_instance", None)
        page_id = self._page_service.get_active_page_id()
        if callable(unlink) and unlink(page_id, master_id):
            self.refresh()
        else:
            QMessageBox.information(
                self,
                _ts("Manage Masters"),
                _ts("The current page has no instance of this master."),
            )

    def _on_delete(self) -> None:
        master = self._selected_master()
        if master is None:
            return
        count = len(self._pages_with_instance(master.id))
        answer = QMessageBox.question(
            self,
            _ts("Manage Masters"),
            _ts(
                "Delete this master definition? {count} page instance(s) stay "
                "on their pages as detached content."
            ).format(count=count),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        masters = [m for m in self._masters() if m.id != master.id]
        if self._write_masters(masters):
            self.refresh()

    @staticmethod
    def _master_payload(master: MasterDefinition) -> dict[str, Any]:
        return masters_namespace_payload([master])["masters"][0]
