"""Local style-library management; document edits are delegated to the dialog."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.appearance import LayerStyleStack
from loomstroke.core.appearance.layer_style_preset import LayerStylePreset
from loomstroke.services.layer_style_preset_store import LayerStylePresetStore
from loomstroke.ui.translations import translated_literal_domain

logger = logging.getLogger(__name__)


def _tr(source: str) -> str:
    return translated_literal_domain("AppearancePanel", source, use_fallback=True)


class LayerStylePresetsTab(QWidget):
    def __init__(
        self,
        store: LayerStylePresetStore,
        current: Callable[[], LayerStyleStack],
        apply: Callable[[LayerStyleStack], None],
    ) -> None:
        super().__init__()
        self.store, self._current, self._apply = store, current, apply
        self._entries: dict[str, LayerStylePreset] = {}
        self._buttons: list[tuple[QPushButton, str]] = []
        self._status = ""
        self._failed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setObjectName("style_presets")
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda _item: self.apply_selected())
        layout.addWidget(self.list, 1)
        self.name = QLineEdit()
        self.name.setMaxLength(128)
        self.name.setObjectName("style_preset_name")
        self.name.textChanged.connect(self._actions)
        layout.addWidget(self.name)
        row = QHBoxLayout()
        self.apply_button = self._button(row, "Apply preset", self.apply_selected)
        self.save_button = self._button(row, "Save new", self.save_current)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.update_button = self._button(row, "Update preset", self.update_current)
        self.rename_button = self._button(row, "Rename", self.rename_selected)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.delete_button = self._button(row, "Delete preset", self.delete_selected)
        self.reload_button = self._button(row, "Reload", self.reload)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.retranslate()
        self.reload()

    def _button(self, row: QHBoxLayout, source: str, slot: Callable[[], None]) -> QPushButton:
        button = QPushButton(_tr(source))
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        self._buttons.append((button, source))
        row.addWidget(button)
        return button

    def selected(self) -> LayerStylePreset | None:
        item = self.list.currentItem()
        return self._entries.get(item.data(256)) if item is not None else None

    def retranslate(self) -> None:
        for button, source in self._buttons:
            button.setText(_tr(source))
        self.name.setPlaceholderText(_tr("Preset name"))
        self.name.setAccessibleName(_tr("Preset name"))
        self.list.setAccessibleName(_tr("Presets"))
        self.status.setText(_tr(self._status) if self._status else "")

    def reload(self, identifier: str | None = None) -> None:
        selected = self.selected()
        identifier = identifier or (selected.preset_id if selected else None)
        try:
            entries = self.store.list_presets()
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self._failed = False
        self._entries = {p.preset_id: p for p in entries}
        self.list.clear()
        for preset in entries:
            item = QListWidgetItem(preset.name)
            item.setData(256, preset.preset_id)
            self.list.addItem(item)
            if preset.preset_id == identifier:
                self.list.setCurrentItem(item)
        self._status = "" if entries else "No presets yet. Save the current styles to create one."
        self.status.setText(_tr(self._status))
        self._actions()

    def _selected(self) -> None:
        preset = self.selected()
        if preset:
            self.name.setText(preset.name)
        self._actions()

    def _actions(self) -> None:
        selected = self.selected() is not None and not self._failed
        named = bool(self.name.text().strip()) and not self._failed
        self.apply_button.setEnabled(selected)
        self.rename_button.setEnabled(selected and named)
        self.update_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)
        self.save_button.setEnabled(named and len(self._entries) < self.store.MAX_PRESETS)

    def _error(self, exc: Exception) -> None:
        logger.warning("Layer style preset operation failed: %s", exc)
        self._failed = True
        self._status = (
            "Could not access presets. The previous library was kept. Check permissions and reload."
        )
        self.status.setText(_tr(self._status))
        self._actions()

    def _operation(self, action: Callable[[], None], identifier: str | None = None) -> None:
        try:
            action()
        except (OSError, ValueError) as exc:
            self._error(exc)
        else:
            self.reload(identifier)

    def save_current(self) -> None:
        try:
            preset = self.store.add(self.name.text(), self._current())
        except (OSError, ValueError) as exc:
            self._error(exc)
        else:
            self.reload(preset.preset_id)

    def apply_selected(self) -> None:
        preset = self.selected()
        if preset and not self._failed:
            self._apply(preset.copied_stack())

    def update_current(self) -> None:
        preset = self.selected()
        if preset:
            self._operation(
                lambda: self.store.update(preset.preset_id, stack=self._current()), preset.preset_id
            )

    def rename_selected(self) -> None:
        preset = self.selected()
        if preset:
            self._operation(
                lambda: self.store.update(preset.preset_id, name=self.name.text()), preset.preset_id
            )

    def delete_selected(self) -> None:
        preset = self.selected()
        if preset:
            self._operation(lambda: self.store.delete(preset.preset_id))
