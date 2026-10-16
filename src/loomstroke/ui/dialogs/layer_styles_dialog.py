"""``Layer Styles...`` dialog: ordered style stack editor with Apply / Rasterize (CT-21 §6)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.appearance import AppearanceRegistry, LayerStyleSpec, LayerStyleStack
from loomstroke.ui.translations import translated_literal_domain

from .appearance_param_form import AppearanceParamForm

__all__ = ["LayerStylesDialog"]

_ROLE = 0x0100


def _ts(text: str) -> str:
    return translated_literal_domain("AppearancePanel", text, use_fallback=True)


class LayerStylesDialog(QDialog):
    def __init__(
        self,
        registry: AppearanceRegistry,
        stack: LayerStyleStack,
        *,
        on_apply: Callable[[LayerStyleStack], bool],
        on_rasterize: Callable[[], str | None] | None = None,
        parent: QWidget | None = None,
        color_picker: Any = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._stack = stack
        self._on_apply = on_apply
        self._on_rasterize = on_rasterize
        self._syncing = False
        self.setWindowTitle(_ts("Layer Styles"))
        self.resize(520, 420)
        self._build(color_picker)
        self._refresh()

    # -- build -------------------------------------------------------------------------

    def _build(self, color_picker: Any) -> None:
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.kind_combo = QComboBox()
        for kind in self._registry.kinds("style"):
            self.kind_combo.addItem(_ts(kind.title), kind.kind_id)
        row.addWidget(self.kind_combo, 1)
        self.add_button = self._button(row, "Add", self.add_selected_kind)
        self.remove_button = self._button(row, "Remove", self.remove_selected)
        self.up_button = self._button(row, "Up", lambda: self.move_selected(-1))
        self.down_button = self._button(row, "Down", lambda: self.move_selected(1))
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.list, 1)
        self.enabled_box = QCheckBox(_ts("Enabled"))
        self.enabled_box.toggled.connect(self._on_enabled)
        layout.addWidget(self.enabled_box)
        self.params = AppearanceParamForm(
            None, self, color_picker=color_picker, on_change=self._on_params_changed
        )
        layout.addWidget(self.params)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        self.rasterize_button = self._button(buttons, "Rasterize Appearance", self.rasterize)
        self.rasterize_button.setEnabled(self._on_rasterize is not None)
        buttons.addStretch(1)
        self.apply_button = self._button(buttons, "Apply", self.apply)
        close = self._button(buttons, "Close", self.reject)
        close.setDefault(False)
        layout.addLayout(buttons)

    def _button(self, row: QHBoxLayout, text: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(_ts(text))
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    # -- state -------------------------------------------------------------------------

    def current_stack(self) -> LayerStyleStack:
        return self._stack

    def selected_style(self) -> LayerStyleSpec | None:
        item = self.list.currentItem()
        return self._stack.get(str(item.data(_ROLE))) if item is not None else None

    def select_style(self, style_id: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(_ROLE) == style_id:
                self.list.setCurrentRow(row)
                return

    def _refresh(self) -> None:
        selected = self.selected_style()
        self._syncing = True
        self.list.clear()
        for style in self._stack.styles:
            kind = self._registry.get("style", style.kind)
            title = _ts(kind.title) if kind else style.kind
            suffix = "" if style.enabled else f"  [{_ts('disabled')}]"
            item = QListWidgetItem(f"{title}{suffix}")
            item.setData(_ROLE, style.style_id)
            self.list.addItem(item)
        self._syncing = False
        if selected is not None:
            self.select_style(selected.style_id)
        self._on_selection()

    def _on_selection(self) -> None:
        if self._syncing:
            return
        style = self.selected_style()
        self._syncing = True
        self.enabled_box.setChecked(bool(style and style.enabled))
        kind = self._registry.get("style", style.kind) if style else None
        self.params.set_kind(kind, style.params if style else None)
        self._syncing = False

    # -- edits -------------------------------------------------------------------------

    def add_selected_kind(self) -> LayerStyleSpec | None:
        kind = self._registry.get("style", str(self.kind_combo.currentData()))
        if kind is None:
            return None
        style = LayerStyleSpec(f"style-{uuid.uuid4().hex[:8]}", kind.kind_id, kind.defaults())
        self._stack = self._stack.with_style(style)
        self._refresh()
        self.select_style(style.style_id)
        return style

    def remove_selected(self) -> None:
        style = self.selected_style()
        if style is not None:
            self._stack = self._stack.without(style.style_id)
            self._refresh()

    def move_selected(self, delta: int) -> None:
        style = self.selected_style()
        if style is not None:
            self._stack = self._stack.moved(style.style_id, delta)
            self._refresh()
            self.select_style(style.style_id)

    def _on_enabled(self, checked: bool) -> None:
        style = self.selected_style()
        if self._syncing or style is None or style.enabled == checked:
            return
        self._stack = self._stack.with_style(style.with_enabled(checked))
        self._refresh()

    def _on_params_changed(self) -> None:
        style = self.selected_style()
        if self._syncing or style is None:
            return
        params, _issues = self._registry.validate("style", style.kind, self.params.values())
        self._stack = self._stack.with_style(style.with_params(params))

    def apply(self) -> bool:
        self._on_params_changed()
        ok = bool(self._on_apply(self._stack))
        self.status_label.setText(_ts("Applied.") if ok else _ts("Could not apply styles."))
        return ok

    def rasterize(self) -> str | None:
        if self._on_rasterize is None:
            return None
        self.apply()
        new_id = self._on_rasterize()
        self.status_label.setText(
            _ts("Rasterized into a new layer.") if new_id else _ts("Nothing to rasterize.")
        )
        return new_id
