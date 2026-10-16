"""User filter editing dialogs for ComfyUI node tabs."""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.translations import translate_with_fallback

from .node_tab_user_filters import UserFilterConfig, UserFilterManager


def _node_tabs_tr(text: str) -> str:
    return translate_with_fallback("NodeTabsWidget", text)


class ManageUserFiltersDialog(QDialog):
    """Simple dialog for managing user filter presets."""

    def __init__(self, filters: list[UserFilterConfig], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_node_tabs_tr("User-defined Filters"))
        self.resize(420, 360)
        self._filters = list(filters)

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.list_widget)

        button_row = QHBoxLayout()
        self._add_btn = QPushButton(_node_tabs_tr("Add"))
        self._add_btn.clicked.connect(self._add_filter)
        button_row.addWidget(self._add_btn)
        self._edit_btn = QPushButton(_node_tabs_tr("Edit"))
        self._edit_btn.clicked.connect(self._edit_filter)
        button_row.addWidget(self._edit_btn)
        self._remove_btn = QPushButton(_node_tabs_tr("Delete"))
        self._remove_btn.clicked.connect(self._remove_filter)
        button_row.addWidget(self._remove_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self._buttons.rejected.connect(self.accept)
        layout.addWidget(self._buttons)

        self._refresh_list()
        self.refresh_translations()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def refresh_translations(self) -> None:
        self.setWindowTitle(_node_tabs_tr("User-defined Filters"))
        self._add_btn.setText(_node_tabs_tr("Add"))
        self._edit_btn.setText(_node_tabs_tr("Edit"))
        self._remove_btn.setText(_node_tabs_tr("Delete"))
        close_btn = self._buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText(_node_tabs_tr("Close"))
        self._refresh_list()

    def result_filters(self) -> list[UserFilterConfig]:
        return list(self._filters)

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for config in self._filters:
            summary = ", ".join(config.name_keywords) or _node_tabs_tr("(No node name conditions)")
            item = QListWidgetItem(f"{config.display_name} — {summary}")
            item.setData(Qt.ItemDataRole.UserRole, config.filter_id)
            self.list_widget.addItem(item)

    def _selected_index(self) -> int | None:
        row = self.list_widget.currentRow()
        return row if row >= 0 else None

    def _add_filter(self) -> None:
        dialog = UserFilterEditDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._filters.append(dialog.get_config())
        self._refresh_list()

    def _edit_filter(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        dialog = UserFilterEditDialog(self._filters[index], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._filters[index] = dialog.get_config()
        self._refresh_list()

    def _remove_filter(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self._filters[index]
        self._refresh_list()


class UserFilterEditDialog(QDialog):
    """Dialog for creating or editing a single user filter."""

    def __init__(
        self,
        config: UserFilterConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_node_tabs_tr("Filter Settings"))
        self.resize(360, 240)
        self._config = config

        layout = QVBoxLayout(self)
        self._form = QFormLayout()
        self._display_name_label = QLabel(_node_tabs_tr("Tab Name"))
        self._name_keywords_label = QLabel(_node_tabs_tr("Node Name Keywords"))
        self._param_keywords_label = QLabel(_node_tabs_tr("Parameter Name Keywords"))
        self._value_types_label = QLabel(_node_tabs_tr("Value Types (image/number, etc.)"))

        self.display_name_edit = QLineEdit(config.display_name if config else "")
        self._form.addRow(self._display_name_label, self.display_name_edit)

        self.name_keywords_edit = QLineEdit(", ".join(config.name_keywords) if config else "")
        self._form.addRow(self._name_keywords_label, self.name_keywords_edit)

        self.param_keywords_edit = QLineEdit(", ".join(config.parameter_keywords) if config else "")
        self._form.addRow(self._param_keywords_label, self.param_keywords_edit)

        self.value_types_edit = QLineEdit(", ".join(config.value_types) if config else "")
        self._form.addRow(self._value_types_label, self.value_types_edit)

        layout.addLayout(self._form)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self.refresh_translations()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def refresh_translations(self) -> None:
        self.setWindowTitle(_node_tabs_tr("Filter Settings"))
        self._display_name_label.setText(_node_tabs_tr("Tab Name"))
        self._name_keywords_label.setText(_node_tabs_tr("Node Name Keywords"))
        self._param_keywords_label.setText(_node_tabs_tr("Parameter Name Keywords"))
        self._value_types_label.setText(_node_tabs_tr("Value Types (image/number, etc.)"))
        ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText(_node_tabs_tr("OK"))
        cancel_btn = self._buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText(_node_tabs_tr("Cancel"))

    def get_config(self) -> UserFilterConfig:
        filter_id = self._config.filter_id if self._config else str(uuid4())
        return UserFilterConfig(
            filter_id=filter_id,
            display_name=self.display_name_edit.text().strip() or _node_tabs_tr("Custom"),
            name_keywords=UserFilterManager.normalize_keywords(self.name_keywords_edit.text()),
            parameter_keywords=UserFilterManager.normalize_keywords(
                self.param_keywords_edit.text()
            ),
            value_types=UserFilterManager.normalize_keywords(self.value_types_edit.text()),
        )


__all__ = ["ManageUserFiltersDialog", "UserFilterEditDialog"]
