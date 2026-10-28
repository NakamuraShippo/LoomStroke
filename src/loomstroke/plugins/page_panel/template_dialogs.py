"""Template capture options + template manager dialogs (CT-07 T2).

Both are thin views over the page service's duck-typed template facades.
Library operations only — nothing here touches the document, history, or
dirty state (spec 07 §9).
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.page_templates import TemplateCaptureOptions
from loomstroke.core.template_raster_codec import TemplateRasterCodec
from loomstroke.ui.translations import translated_literal_domain

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    return translated_literal_domain("PagePanelPlugin", text, use_fallback=True)


class TemplateCaptureDialog(QDialog):
    """Name + capture options for ``Save Current Page as Template...``."""

    def __init__(
        self,
        *,
        suggested_name: str,
        estimated_raster_bytes: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_ts("Save Page Template"))
        self._estimated_raster_bytes = max(0, int(estimated_raster_bytes))

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_ts("Template name:"), self))
        self.name_edit = QLineEdit(self)
        self.name_edit.setText(suggested_name)
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)

        self.include_vectors_box = QCheckBox(_ts("Include vector objects"), self)
        self.include_vectors_box.setChecked(True)
        layout.addWidget(self.include_vectors_box)
        self.include_guides_box = QCheckBox(_ts("Include guides"), self)
        self.include_guides_box.setChecked(False)
        layout.addWidget(self.include_guides_box)
        # CT-07 T2: raster artwork is opt-in — it lives inside the JSON
        # library, so the estimate and a warning are shown up front.
        self.include_raster_box = QCheckBox(
            _ts("Include raster artwork (stored inside the template)"), self
        )
        self.include_raster_box.setChecked(False)
        layout.addWidget(self.include_raster_box)
        self.raster_size_label = QLabel(self)
        self.raster_size_label.setWordWrap(True)
        layout.addWidget(self.raster_size_label)
        self.include_raster_box.toggled.connect(lambda _c: self._refresh_raster_note())
        self._refresh_raster_note()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def template_name(self) -> str:
        return self.name_edit.text().strip()

    def capture_options(self) -> TemplateCaptureOptions:
        return TemplateCaptureOptions(
            include_vector_objects=self.include_vectors_box.isChecked(),
            include_guides=self.include_guides_box.isChecked(),
            include_raster_artwork=self.include_raster_box.isChecked(),
        )

    def _refresh_raster_note(self) -> None:
        if not self.include_raster_box.isChecked():
            self.raster_size_label.setText("")
            return
        size = self._estimated_raster_bytes
        shown = (
            f"{size / (1024 * 1024):.1f} MiB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KiB"
        )
        text = _ts("Raster artwork: about {size} uncompressed.").format(size=shown)
        if size > TemplateRasterCodec.WARN_BYTES:
            text += " " + _ts("Large artwork bloats the template library.")
        self.raster_size_label.setText(text)


class TemplateManagerDialog(QDialog):
    """Built-in / user template listing with user-only edit operations."""

    def __init__(self, *, page_service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page_service = page_service
        self.setWindowTitle(_ts("Manage Templates"))
        self.resize(420, 360)

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget(self)
        self.list_widget.itemSelectionChanged.connect(self._refresh_buttons)
        layout.addWidget(self.list_widget, 1)

        row = QHBoxLayout()
        self.rename_button = QPushButton(_ts("Rename..."), self)
        self.rename_button.clicked.connect(self._on_rename)
        self.duplicate_button = QPushButton(_ts("Duplicate"), self)
        self.duplicate_button.clicked.connect(self._on_duplicate)
        self.delete_button = QPushButton(_ts("Delete"), self)
        self.delete_button.clicked.connect(self._on_delete)
        self.category_button = QPushButton(_ts("Category..."), self)
        self.category_button.clicked.connect(self._on_category)
        for button in (
            self.rename_button,
            self.duplicate_button,
            self.delete_button,
            self.category_button,
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
        for summary in self._summaries():
            source_label = _ts("Built-in") if summary.source == "builtin" else _ts("User")
            category = str(getattr(summary, "category", "") or "")
            label = (
                f"{summary.name}  [{category}, {source_label}]"
                if category
                else (f"{summary.name}  [{source_label}]")
            )
            item = QListWidgetItem(label, self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, summary.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, summary.source)
            preview = self._preview_pixmap(summary.id)
            if preview is not None:
                item.setIcon(QIcon(preview))
            self.list_widget.addItem(item)
            if summary.id == selected:
                self.list_widget.setCurrentItem(item)
        self._refresh_buttons()

    def _summaries(self) -> list[Any]:
        list_templates = getattr(self._page_service, "list_page_templates", None)
        if not callable(list_templates):
            return []
        return list(list_templates() or [])

    def _selected_id(self) -> str | None:
        item = self.list_widget.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, str) else None

    def _selected_is_user(self) -> bool:
        item = self.list_widget.currentItem()
        return bool(item) and item.data(Qt.ItemDataRole.UserRole + 1) != "builtin"

    def _preview_pixmap(self, template_id: str) -> QPixmap | None:
        reader = getattr(self._page_service, "get_page_template_preview", None)
        if not callable(reader):
            return None
        try:
            payload = reader(template_id)
        except Exception:
            return None
        if not payload:
            return None
        pixmap = QPixmap()
        return pixmap if pixmap.loadFromData(payload, "PNG") else None

    def _refresh_buttons(self) -> None:
        has_selection = self._selected_id() is not None
        is_user = self._selected_is_user()
        self.rename_button.setEnabled(is_user)
        self.delete_button.setEnabled(is_user)
        self.category_button.setEnabled(is_user)
        # Duplicating a built-in into a user template is how presets get
        # customized, so this one stays enabled for both.
        self.duplicate_button.setEnabled(has_selection)

    # ------------------------------------------------------------------
    def _on_rename(self) -> None:
        template_id = self._selected_id()
        if template_id is None or not self._selected_is_user():
            return
        current = self.list_widget.currentItem()
        current_name = current.text().rsplit("  [", 1)[0] if current else ""
        name, ok = QInputDialog.getText(
            self, _ts("Rename Template"), _ts("Template name:"), text=current_name
        )
        if not ok or not name.strip():
            return
        rename = getattr(self._page_service, "rename_page_template", None)
        self._run_library_operation(rename, template_id, name.strip())

    def _on_category(self) -> None:
        template_id = self._selected_id()
        if template_id is None or not self._selected_is_user():
            return
        category, ok = QInputDialog.getText(
            self, _ts("Template Category"), _ts("Template category:"), text="custom"
        )
        if not ok or not category.strip():
            return
        set_category = getattr(self._page_service, "set_page_template_category", None)
        self._run_library_operation(set_category, template_id, category.strip())

    def _on_duplicate(self) -> None:
        template_id = self._selected_id()
        if template_id is None:
            return
        duplicate = getattr(self._page_service, "duplicate_page_template", None)
        self._run_library_operation(duplicate, template_id)

    def _on_delete(self) -> None:
        template_id = self._selected_id()
        if template_id is None or not self._selected_is_user():
            return
        answer = QMessageBox.question(
            self,
            _ts("Delete Template"),
            _ts("Delete this template? This cannot be undone."),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete = getattr(self._page_service, "delete_page_template", None)
        self._run_library_operation(delete, template_id)

    def _run_library_operation(self, operation: Any, *args: Any) -> None:
        try:
            succeeded = callable(operation) and bool(operation(*args))
        except Exception:
            logger.warning("Template library operation did not complete")
            succeeded = False
        if succeeded:
            self.refresh()
        else:
            QMessageBox.warning(
                self,
                _ts("Manage Templates"),
                _ts("The template could not be saved. See the log for details."),
            )
