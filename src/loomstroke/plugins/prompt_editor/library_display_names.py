"""Library editor for optional LivePrompt names of String and List values."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.common.constants import CK_LIST, CK_STRING, SK_LIST, SK_TEXT
from loomstroke.plugins.common.snippet_models import Snippet
from loomstroke.ui.translations import translated_literal_domain

from .display_names import (
    LIST_DISPLAY_NAMES_KEY,
    STRING_DISPLAY_NAME_KEY,
    display_name_for,
    resolved_list_items,
    store_display_names,
)


def _tr(text: str) -> str:
    return translated_literal_domain("PromptEditorPanel", text, use_fallback=True)


class LibraryDisplayNamesEditor(QWidget):
    """Edit labels separately from values that are sent to image generators."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = ""
        self._body = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.label = QLabel(self)
        layout.addWidget(self.label)
        self.string_name = QLineEdit(self)
        self.string_name.textChanged.connect(lambda _text: self.changed.emit())
        layout.addWidget(self.string_name)
        self.list_names = QTableWidget(0, 2, self)
        self.list_names.setMinimumHeight(88)
        self.list_names.setMaximumHeight(150)
        self.list_names.verticalHeader().hide()
        self.list_names.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.list_names.itemChanged.connect(lambda _item: self.changed.emit())
        layout.addWidget(self.list_names)
        self.retranslate()
        self.setVisible(False)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    def retranslate(self) -> None:
        self.label.setText(_tr("Display name"))
        self.string_name.setPlaceholderText(_tr("Display name"))
        self.list_names.setHorizontalHeaderLabels([_tr("Item"), _tr("Display name")])

    def set_entry(self, snippet: Snippet | None) -> None:
        kind = snippet.kind if snippet is not None else ""
        body = snippet.body if snippet is not None else ""
        self._kind = kind
        self._body = body
        self.setVisible(kind in (SK_TEXT, SK_LIST))
        self.string_name.setVisible(kind == SK_TEXT)
        self.list_names.setVisible(kind == SK_LIST)
        name = display_name_for(snippet, CK_STRING) if snippet is not None else ""
        previous = self.string_name.blockSignals(True)
        self.string_name.setText(name)
        self.string_name.blockSignals(previous)
        items = resolved_list_items(snippet) if snippet is not None and kind == SK_LIST else []
        names = [display_name_for(snippet, CK_LIST, i) for i in range(len(items))]
        self._set_rows(items, names)

    def set_kind(self, kind: str) -> None:
        if kind == self._kind:
            return
        self._kind = kind
        self.setVisible(kind in (SK_TEXT, SK_LIST))
        self.string_name.setVisible(kind == SK_TEXT)
        self.list_names.setVisible(kind == SK_LIST)
        if kind == SK_LIST:
            self._set_rows(self._items_for_body(self._body), [])

    def sync_body(self, body: str) -> None:
        if body == self._body:
            return
        self._body = body
        if self._kind != SK_LIST:
            return
        old = self.values_and_names()
        items = self._items_for_body(body)
        names = [
            old[index][1] if index < len(old) and old[index][0] == value else ""
            for index, value in enumerate(items)
        ]
        self._set_rows(items, names)

    def values_and_names(self) -> list[tuple[str, str]]:
        return [
            (self.list_names.item(row, 0).text(), self.list_names.item(row, 1).text())
            for row in range(self.list_names.rowCount())
        ]

    def apply_metadata(self, metadata: dict[str, Any]) -> None:
        if self._kind == SK_TEXT:
            store_display_names(metadata, CK_STRING, [], [self.string_name.text()])
        elif self._kind == SK_LIST:
            pairs = self.values_and_names()
            store_display_names(
                metadata,
                CK_LIST,
                [value for value, _name in pairs],
                [name for _value, name in pairs],
            )
        else:
            metadata.pop(STRING_DISPLAY_NAME_KEY, None)
            metadata.pop(LIST_DISPLAY_NAMES_KEY, None)

    @staticmethod
    def _items_for_body(body: str) -> list[str]:
        return resolved_list_items(Snippet(id="", title="", body=body, kind=SK_LIST))

    def _set_rows(self, items: list[str], names: list[str]) -> None:
        previous = self.list_names.blockSignals(True)
        self.list_names.setRowCount(len(items))
        for index, value in enumerate(items):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.list_names.setItem(index, 0, item)
            self.list_names.setItem(
                index, 1, QTableWidgetItem(names[index] if index < len(names) else "")
            )
        self.list_names.blockSignals(previous)
