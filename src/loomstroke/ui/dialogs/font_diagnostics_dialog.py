"""Font Diagnostics dialog (CT-25 spec §ユーザーフロー Font Diagnostics / Substitute).

Lists every requested font with its resolution status, pages and object
counts; offers an explicit substitution (target family + scope) and
conversion of the selected font's text objects to outlines.  The dialog
holds no font logic: the action supplies callbacks and reports outcomes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.fonts.font_request import FontRequest
from ...core.fonts.font_usage import FontUsage
from ...core.fonts.font_usage_inventory import FontUsageInventory
from ..translations import translate_with_fallback

__all__ = ["FontDiagnosticsDialog"]

SubstituteCallback = Callable[[FontUsage, FontRequest, str], str]
OutlineCallback = Callable[[FontUsage], str]


def _ts(text: str) -> str:
    return translate_with_fallback("FontDiagnosticsDialog", text)


class FontDiagnosticsDialog(QDialog):
    def __init__(
        self,
        *,
        inventory: FontUsageInventory,
        families: Sequence[str],
        on_substitute: SubstituteCallback | None = None,
        on_outline: OutlineCallback | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_ts("Font Diagnostics"))
        self._inventory = inventory
        self._on_substitute = on_substitute
        self._on_outline = on_outline
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(
            [_ts("Font"), _ts("Status"), _ts("Pages"), _ts("Objects"), _ts("Characters")]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.target_combo = QComboBox(self)
        for family in families:
            self.target_combo.addItem(family, family)
        self.scope_combo = QComboBox(self)
        self.scope_combo.addItem(_ts("Current page"), "page")
        self.scope_combo.addItem(_ts("Whole document"), "document")
        self.substitute_button = QPushButton(_ts("Substitute..."), self)
        self.outline_button = QPushButton(_ts("Convert to Outlines..."), self)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        row = QHBoxLayout()
        row.addWidget(QLabel(_ts("Substitute with:"), self))
        row.addWidget(self.target_combo, 1)
        row.addWidget(QLabel(_ts("Scope:"), self))
        row.addWidget(self.scope_combo)
        row.addWidget(self.substitute_button)
        row.addWidget(self.outline_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)
        self.resize(720, 420)

        self.substitute_button.clicked.connect(self.substitute_selected)
        self.outline_button.clicked.connect(self.outline_selected)
        self.table.itemSelectionChanged.connect(self._refresh_buttons)
        self.set_inventory(inventory)

    # -- state -------------------------------------------------------------------

    def set_inventory(self, inventory: FontUsageInventory) -> None:
        self._inventory = inventory
        self.table.setRowCount(len(inventory.usages))
        for row, usage in enumerate(inventory.usages):
            resolution = inventory.resolution_for(usage)
            status = resolution.status if resolution is not None else "unknown"
            if resolution is not None and resolution.missing_glyphs:
                status += f" ({resolution.missing_glyphs} missing glyphs)"
            cells = (
                usage.request.label or _ts("(default font)"),
                _ts(status),
                str(len(usage.page_ids)),
                str(usage.object_count),
                str(usage.char_count),
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, usage.request.key)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        counts = inventory.counts()
        self.status_label.setText(
            _ts("{fonts} font(s), {missing} missing, {objects} text object(s).").format(**counts)
        )
        self._refresh_buttons()

    def selected_usage(self) -> FontUsage | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        for usage in self._inventory.usages:
            if usage.request.key == key:
                return usage
        return None

    def select_row(self, row: int) -> None:
        self.table.selectRow(row)

    def _refresh_buttons(self) -> None:
        has = self.selected_usage() is not None
        self.substitute_button.setEnabled(has and self._on_substitute is not None)
        self.outline_button.setEnabled(has and self._on_outline is not None)

    # -- actions -----------------------------------------------------------------

    def substitute_selected(self) -> str | None:
        usage = self.selected_usage()
        if usage is None or self._on_substitute is None:
            return None
        target = FontRequest(family=str(self.target_combo.currentData() or ""))
        if not target.family:
            return None
        message = self._on_substitute(usage, target, str(self.scope_combo.currentData() or "page"))
        self.status_label.setText(message)
        return message

    def outline_selected(self) -> str | None:
        usage = self.selected_usage()
        if usage is None or self._on_outline is None:
            return None
        answer = QMessageBox.question(
            self,
            _ts("Convert to Outlines"),
            _ts(
                "Convert the text objects using this font on the current page to outlines? "
                "They stop being editable text (searchability is lost); the original text is "
                "kept for restore."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
        message = self._on_outline(usage)
        self.status_label.setText(message)
        return message
