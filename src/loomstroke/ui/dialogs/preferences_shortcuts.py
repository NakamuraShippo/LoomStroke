"""Helpers for the shortcuts preferences page."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, cast

from PySide6.QtCore import QT_TRANSLATE_NOOP, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
)

from ...platform.shortcut_manager import ShortcutAction

TranslateText = Callable[[str], str]
OwnerLabel = Callable[[ShortcutAction], str]
CategorySortKey = Callable[[str], tuple[str, int]]


class MouseBindingHelper:
    """Owns mouse gesture binding definitions and settings access."""

    def __init__(self, settings: Any, translate_text: TranslateText) -> None:
        self._settings = settings
        self._translate_text = translate_text
        self._definitions = self.build_definitions()
        self._action_ids = {definition["action_id"] for definition in self._definitions}
        self._setting_keys = {definition["setting_key"] for definition in self._definitions}

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return self._definitions

    @property
    def action_ids(self) -> set[str]:
        return set(self._action_ids)

    @property
    def setting_keys(self) -> set[str]:
        return set(self._setting_keys)

    @staticmethod
    def build_definitions() -> list[dict[str, Any]]:
        return [
            {
                "action_id": "input.mouse_pan_gesture",
                "label": cast(
                    str,
                    QT_TRANSLATE_NOOP("ShortcutsPage", "Mouse: Pan gesture"),
                ),
                "description": cast(
                    str,
                    QT_TRANSLATE_NOOP(
                        "ShortcutsPage",
                        "Choose the mouse gesture used to pan the canvas.",
                    ),
                ),
                "category": cast(
                    str,
                    QT_TRANSLATE_NOOP("ShortcutsPage", "Mouse Operations"),
                ),
                "owner": cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "LoomStroke Core")),
                "setting_key": "input/pan_gesture",
                "default": "space_left",
                "options": [
                    (cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Right drag")), "right"),
                    (cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Middle drag")), "middle"),
                    (
                        cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Space + left drag")),
                        "space_left",
                    ),
                    (cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Alt + left drag")), "alt_left"),
                    (
                        cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Shift + left drag")),
                        "shift_left",
                    ),
                    (
                        cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Ctrl + left drag")),
                        "ctrl_left",
                    ),
                ],
            },
            {
                "action_id": "input.mouse_context_menu",
                "label": cast(
                    str,
                    QT_TRANSLATE_NOOP("ShortcutsPage", "Mouse: Context menu"),
                ),
                "description": cast(
                    str,
                    QT_TRANSLATE_NOOP(
                        "ShortcutsPage",
                        "Choose the mouse gesture used to open the canvas context menu.",
                    ),
                ),
                "category": cast(
                    str,
                    QT_TRANSLATE_NOOP("ShortcutsPage", "Mouse Operations"),
                ),
                "owner": cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "LoomStroke Core")),
                "setting_key": "input/context_menu_gesture",
                "default": "right_click",
                "options": [
                    (cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Right click")), "right_click"),
                    (
                        cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Middle click")),
                        "middle_click",
                    ),
                    (
                        cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Space + left click")),
                        "space_left_click",
                    ),
                    (
                        cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Alt + left click")),
                        "alt_left_click",
                    ),
                    (
                        cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Shift + left click")),
                        "shift_left_click",
                    ),
                    (
                        cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Ctrl + left click")),
                        "ctrl_left_click",
                    ),
                ],
            },
            {
                "action_id": "input.brush_size_key_acceleration",
                "label": cast(
                    str,
                    QT_TRANSLATE_NOOP("ShortcutsPage", "Keyboard: Brush size acceleration"),
                ),
                "description": cast(
                    str,
                    QT_TRANSLATE_NOOP(
                        "ShortcutsPage",
                        "How much repeated [ / ] presses speed up brush size changes.",
                    ),
                ),
                "category": cast(
                    str,
                    QT_TRANSLATE_NOOP("ShortcutsPage", "Keyboard Operations"),
                ),
                "owner": cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "LoomStroke Core")),
                "setting_key": "input/brush_size_key_acceleration",
                "default": "standard",
                "options": [
                    (cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Off")), "none"),
                    (cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Weak")), "weak"),
                    (cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Standard")), "standard"),
                    (cast(str, QT_TRANSLATE_NOOP("ShortcutsPage", "Strong")), "strong"),
                ],
            },
        ]

    def definition(self, action_id: str) -> dict[str, Any] | None:
        for definition in self._definitions:
            if definition["action_id"] == action_id:
                return definition
        return None

    def entries(self) -> list[ShortcutAction]:
        entries: list[ShortcutAction] = []
        for definition in self._definitions:
            current_value = self.read_value(definition)
            current_label = self.display_label(definition, current_value)
            entries.append(
                ShortcutAction(
                    action_id=definition["action_id"],
                    label=self._translate_text(definition["label"]),
                    category=definition["category"],
                    description=self._translate_text(definition["description"]),
                    default=None,
                    current=current_label,
                    owner=definition.get("owner"),
                )
            )
        return entries

    def read_value(self, definition: dict[str, Any]) -> str:
        default = str(definition["default"])
        value = self._settings.get(definition["setting_key"], default)
        if not isinstance(value, str):
            return default
        return value

    def display_label(self, definition: dict[str, Any], value: str) -> str:
        for label, option_value in definition["options"]:
            if option_value == value:
                return self._translate_text(label)
        return value

    def options(self, definition: dict[str, Any]) -> list[tuple[str, str]]:
        return [(self._translate_text(label), value) for label, value in definition["options"]]


class ShortcutConflictDetector:
    """Computes duplicate shortcut assignments without owning UI widgets."""

    def __init__(self, translate_text: TranslateText) -> None:
        self._translate_text = translate_text

    def compute(self, entries: Iterable[ShortcutAction]) -> dict[str, list[str]]:
        collisions: dict[str, list[ShortcutAction]] = {}
        for entry in entries:
            sequence = (entry.current or "").strip()
            if not sequence:
                continue
            collisions.setdefault(sequence.lower(), []).append(entry)
        conflicts: dict[str, list[str]] = {}
        for conflict_entries in collisions.values():
            if len(conflict_entries) <= 1:
                continue
            labels = [
                self._translate_text(conflict_entry.label) for conflict_entry in conflict_entries
            ]
            for entry in conflict_entries:
                entry_label = self._translate_text(entry.label)
                conflicts[entry.action_id] = [label for label in labels if label != entry_label]
        return conflicts


class ShortcutFilterController:
    """Owns shortcut filter widgets and category selection state."""

    def __init__(
        self,
        *,
        category_role: int,
        owner_role: int,
    ) -> None:
        self._category_role = category_role
        self._owner_role = owner_role
        self.filter_edit = QLineEdit()
        self.clear_filter_btn = QPushButton()
        self.assigned_check = QCheckBox()
        self.conflicts_check = QCheckBox()
        self.reset_filters_btn = QPushButton()
        self.category_tree = QTreeWidget()
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setMaximumWidth(220)
        self.category_filter: tuple[str | None, str | None] = (None, None)

    def reset_text_filter(self) -> None:
        self.filter_edit.clear()

    def reset_filters(self) -> None:
        self.filter_edit.clear()
        self.assigned_check.setChecked(False)
        self.conflicts_check.setChecked(False)
        self.select_root_category()

    def filter_entries(
        self,
        entries: Iterable[ShortcutAction],
        conflicts: dict[str, list[str]],
        *,
        translate_text: TranslateText,
        owner_label: OwnerLabel,
    ) -> list[ShortcutAction]:
        filtered = list(entries)
        category, owner = self.category_filter
        if category:
            filtered = [entry for entry in filtered if entry.category == category]
        if owner:
            filtered = [entry for entry in filtered if owner_label(entry) == owner]
        needle = self.filter_edit.text().strip().lower()
        if needle:
            filtered = [
                entry
                for entry in filtered
                if needle in translate_text(entry.label).lower()
                or needle in entry.action_id.lower()
                or (entry.description and needle in translate_text(entry.description).lower())
            ]
        if self.assigned_check.isChecked():
            filtered = [entry for entry in filtered if (entry.current or "").strip()]
        if self.conflicts_check.isChecked():
            filtered = [entry for entry in filtered if entry.action_id in conflicts]
        return filtered

    def build_category_tree(
        self,
        entries: Iterable[ShortcutAction],
        *,
        owner_label: OwnerLabel,
        translate_text: TranslateText,
        category_sort_key: CategorySortKey,
        all_shortcuts_label: str,
    ) -> None:
        previous = self.category_filter
        self.category_tree.blockSignals(True)
        self.category_tree.clear()
        root = QTreeWidgetItem([all_shortcuts_label])
        root.setData(0, self._category_role, None)
        root.setData(0, self._owner_role, None)
        self.category_tree.addTopLevelItem(root)

        summary: dict[str, dict[str, int]] = {}
        for entry in entries:
            entry_owner_label = owner_label(entry)
            category_counts = summary.setdefault(entry.category, {})
            category_counts[entry_owner_label] = category_counts.get(entry_owner_label, 0) + 1

        for category in sorted(summary, key=category_sort_key):
            owners = summary[category]
            total = sum(owners.values())
            cat_item = QTreeWidgetItem([f"{translate_text(category)} ({total})"])
            cat_item.setData(0, self._category_role, category)
            cat_item.setData(0, self._owner_role, None)
            root.addChild(cat_item)
            for entry_owner_label, count in sorted(owners.items()):
                owner_item = QTreeWidgetItem([f"{translate_text(entry_owner_label)} ({count})"])
                owner_item.setData(0, self._category_role, category)
                owner_item.setData(0, self._owner_role, entry_owner_label)
                cat_item.addChild(owner_item)
        root.setExpanded(True)

        target = self.find_category_item(previous)
        self.category_tree.setCurrentItem(target or root)
        self.category_tree.blockSignals(False)

    def find_category_item(self, needle: tuple[str | None, str | None]) -> QTreeWidgetItem | None:
        category, owner = needle
        if category is None and owner is None:
            return self.category_tree.topLevelItem(0)
        top_level_count = self.category_tree.topLevelItemCount()
        for idx in range(top_level_count):
            item = self.category_tree.topLevelItem(idx)
            if item is None:
                continue
            data_category = item.data(0, self._category_role)
            data_owner = item.data(0, self._owner_role)
            if data_category == category and data_owner == owner:
                return item
            for child_idx in range(item.childCount()):
                child = cast(QTreeWidgetItem | None, item.child(child_idx))
                if child is None:
                    continue
                data_category = child.data(0, self._category_role)
                data_owner = child.data(0, self._owner_role)
                if data_category == category and data_owner == owner:
                    return child
        return None

    def select_root_category(self) -> None:
        root = self.category_tree.topLevelItem(0)
        if root is not None:
            self.category_tree.setCurrentItem(root)

    def update_category_selection(self, current: QTreeWidgetItem | None) -> None:
        if current is None:
            self.category_filter = (None, None)
            return
        category = current.data(0, self._category_role)
        owner = current.data(0, self._owner_role)
        category_value = category if isinstance(category, str) and category else None
        owner_value = owner if isinstance(owner, str) and owner else None
        self.category_filter = (category_value, owner_value)


class ShortcutTableModel:
    """Owns the shortcuts table widget and visible row state."""

    def __init__(
        self,
        *,
        translate_text: TranslateText,
        owner_label: OwnerLabel,
        summary_label: QLabel,
        summary_template: str,
    ) -> None:
        self._translate_text = translate_text
        self._owner_label = owner_label
        self._summary_label = summary_label
        self._summary_template = summary_template
        self._table_headers = [
            "Action",
            "Shortcut",
            "Category",
            "Owner",
            "Status",
        ]
        self._table = QTableWidget(0, len(self._table_headers))
        self.apply_headers()
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(self._table.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(self._table.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._entries: list[ShortcutAction] = []
        self._visible_entries: list[ShortcutAction] = []
        self._conflicts: dict[str, list[str]] = {}
        self._summary_counts: tuple[int, int] = (0, 0)

    @property
    def table(self) -> QTableWidget:
        return self._table

    @property
    def table_headers(self) -> list[str]:
        return self._table_headers

    @property
    def entries(self) -> list[ShortcutAction]:
        return self._entries

    @property
    def visible_entries(self) -> list[ShortcutAction]:
        return self._visible_entries

    @property
    def conflicts(self) -> dict[str, list[str]]:
        return self._conflicts

    @property
    def summary_template(self) -> str:
        return self._summary_template

    @summary_template.setter
    def summary_template(self, value: str) -> None:
        self._summary_template = value

    @property
    def summary_counts(self) -> tuple[int, int]:
        return self._summary_counts

    def set_entries(
        self,
        entries: list[ShortcutAction],
        conflicts: dict[str, list[str]],
    ) -> None:
        self._entries = entries
        self._conflicts = conflicts

    def apply_headers(self) -> None:
        headers = [self._translate_text(text) for text in self._table_headers]
        self._table.setHorizontalHeaderLabels(headers)

    def populate_rows(self, entries: Iterable[ShortcutAction]) -> None:
        entries = list(entries)
        self._table.setSortingEnabled(False)
        self._table.clearContents()
        self._table.setRowCount(len(entries))
        self._visible_entries = entries
        for row, entry in enumerate(entries):
            label_text = self._translate_text(entry.label)
            description_text = (
                self._translate_text(entry.description) if entry.description else None
            )
            name_item = QTableWidgetItem(label_text)
            if description_text:
                name_item.setToolTip(description_text)
            name_item.setData(Qt.ItemDataRole.UserRole, entry.action_id)

            shortcut_text = entry.current or "\N{EM DASH}"
            shortcut_item = QTableWidgetItem(shortcut_text)
            category_item = QTableWidgetItem(self._translate_text(entry.category))
            entry_owner_label = self._owner_label(entry)
            owner_item = QTableWidgetItem(self._translate_text(entry_owner_label))

            conflict_labels = self._conflicts.get(entry.action_id)
            status_item = QTableWidgetItem(
                self._translate_text("Conflict") if conflict_labels else ""
            )
            if conflict_labels:
                shortcut_item.setForeground(QColor("#c62828"))
                status_item.setToolTip(
                    self._translate_text("Conflicts with: {labels}").format(
                        labels=", ".join(conflict_labels)
                    )
                )

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, shortcut_item)
            self._table.setItem(row, 2, category_item)
            self._table.setItem(row, 3, owner_item)
            self._table.setItem(row, 4, status_item)
        self._table.resizeRowsToContents()
        self._summary_counts = (len(entries), len(self._entries))
        self.update_summary_label()

    def update_summary_label(self) -> None:
        shown, total = self._summary_counts
        text = self._summary_template.format(shown=shown, total=total)
        self._summary_label.setText(text)
