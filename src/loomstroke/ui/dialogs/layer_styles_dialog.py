"""Two-pane layer styles editor; edits are published while the dialog is open."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.appearance import AppearanceRegistry, LayerStyleSpec, LayerStyleStack
from loomstroke.core.appearance.style_blending import STYLE_BLEND_MODES
from loomstroke.services.layer_style_preset_store import LayerStylePresetStore
from loomstroke.ui.translations import translated_literal_domain

from .appearance_param_form import AppearanceParamForm
from .layer_style_presets_tab import LayerStylePresetsTab
from .layer_style_switch import LayerStyleSwitch

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
        can_apply: Callable[[], bool] | None = None,
        parent: QWidget | None = None,
        color_picker: Any = None,
        preset_store: LayerStylePresetStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._stack = stack
        self._last_applied = stack
        self._on_apply = on_apply
        self._on_rasterize = on_rasterize
        self._can_apply = can_apply or (lambda: True)
        self._syncing = False
        self._drafts: dict[str, LayerStyleSpec] = {}
        self.effect_switches: dict[str, LayerStyleSwitch] = {}
        self._buttons: list[tuple[QPushButton, str]] = []
        self._status_source = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._publish_live)
        self.setWindowTitle(_ts("Layer Styles"))
        self.resize(820, 600)
        self._preset_store = preset_store or LayerStylePresetStore()
        self._build(color_picker)
        self._refresh()

    def _build(self, color_picker: Any) -> None:
        layout = QVBoxLayout(self)
        self.splitter = QSplitter(self)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setMinimumWidth(225)
        self.list.itemSelectionChanged.connect(self._on_selection)
        column.addWidget(self.list, 1)
        self.kind_combo = QComboBox()
        for kind in self._registry.kinds("style"):
            self.kind_combo.addItem(_ts(kind.title), kind.kind_id)
        row = QHBoxLayout()
        row.addWidget(self.kind_combo, 1)
        self.add_button = self._button(row, "Add", self.add_selected_kind)
        column.addLayout(row)
        row = QHBoxLayout()
        self.remove_button = self._button(row, "Remove", self.remove_selected)
        self.up_button = self._button(row, "Up", lambda: self.move_selected(-1))
        self.down_button = self._button(row, "Down", lambda: self.move_selected(1))
        column.addLayout(row)
        self.left_tabs = QTabWidget()
        self.left_tabs.setObjectName("layer_style_tabs")
        self.left_tabs.addTab(left, _ts("Effects"))
        self.presets = LayerStylePresetsTab(
            self._preset_store, self.current_stack, self._apply_preset
        )
        self.left_tabs.addTab(self.presets, _ts("Presets"))
        right = QScrollArea()
        right.setWidgetResizable(True)
        right.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        right.setWidget(content)
        column = QVBoxLayout(content)
        column.setContentsMargins(12, 0, 0, 0)
        self.parameter_title = QLabel()
        column.addWidget(self.parameter_title)
        self._build_blend_controls(column)
        self.params = AppearanceParamForm(
            None,
            self,
            color_picker=color_picker,
            on_change=self._on_params_changed,
            numeric_sliders=True,
        )
        column.addWidget(self.params)
        column.addStretch(1)
        self.splitter.addWidget(self.left_tabs)
        self.splitter.addWidget(right)
        self.splitter.setSizes([260, 500])
        layout.addWidget(self.splitter, 1)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        self.rasterize_button = self._button(buttons, "Rasterize Appearance", self.rasterize)
        self.rasterize_button.setEnabled(self._on_rasterize is not None)
        buttons.addStretch(1)
        self._button(buttons, "Close", self.accept).setDefault(False)
        layout.addLayout(buttons)

    def _build_blend_controls(self, column: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self.blend_label = QLabel(_ts("Blend mode"))
        self.blend_combo = QComboBox()
        self.blend_combo.setObjectName("style_blend_mode")
        self.blend_combo.setAccessibleName(_ts("Blend mode"))
        self.blend_combo.currentIndexChanged.connect(self._on_blend_changed)
        row.addWidget(self.blend_label)
        row.addWidget(self.blend_combo, 1)
        column.addLayout(row)

    def _button(self, row: QHBoxLayout, text: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(_ts(text))
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        row.addWidget(button)
        self._buttons.append((button, text))
        return button

    def current_stack(self) -> LayerStyleStack:
        return self._stack

    def selected_style(self) -> LayerStyleSpec | None:
        item = self.list.currentItem()
        if item is None:
            return None
        style_id = str(item.data(_ROLE))
        return self._stack.get(style_id) or self._drafts.get(style_id)

    def select_style(self, style_id: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(_ROLE) == style_id:
                self.list.setCurrentRow(row)
                return

    def _refresh(self, selected_id: str | None = None) -> None:
        selected = self.selected_style()
        selected_id = selected_id or (selected.style_id if selected else None)
        self._syncing = True
        self.list.clear()
        self.effect_switches.clear()
        rows = list(self._stack.styles)
        for kind in self._registry.kinds("style"):
            if not any(style.kind == kind.kind_id for style in rows):
                draft = next((s for s in self._drafts.values() if s.kind == kind.kind_id), None)
                if draft is None:
                    draft = LayerStyleSpec(
                        f"style-{uuid.uuid4().hex[:8]}",
                        kind.kind_id,
                        kind.defaults(),
                        enabled=False,
                    )
                    self._drafts[draft.style_id] = draft
                rows.append(draft)
        for style in rows:
            self._add_row(style)
        self._syncing = False
        if selected_id:
            self.select_style(selected_id)
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self._on_selection()

    def _add_row(self, style: LayerStyleSpec) -> None:
        kind = self._registry.get("style", style.kind)
        title = _ts(kind.title) if kind else style.kind
        item = QListWidgetItem()
        item.setData(_ROLE, style.style_id)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, title)
        self.list.addItem(item)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(8, 5, 6, 5)
        row.addWidget(QLabel(title), 1)
        switch = LayerStyleSwitch(title, holder)
        switch.setObjectName(f"{style.style_id}_enabled")
        switch.setChecked(style.enabled)
        switch.setEnabled(
            self._stack.get(style.style_id) is not None
            or len(self._stack.styles) < LayerStyleStack.MAX_STYLES
        )
        switch.toggled.connect(lambda checked, sid=style.style_id: self._on_enabled(sid, checked))
        self.effect_switches.setdefault(style.kind, switch)
        self.effect_switches[style.style_id] = switch
        row.addWidget(switch)
        item.setSizeHint(holder.sizeHint())
        self.list.setItemWidget(item, holder)

    def _on_selection(self) -> None:
        if self._syncing:
            return
        style = self.selected_style()
        self._syncing = True
        kind = self._registry.get("style", style.kind) if style else None
        self.parameter_title.setText(_ts(kind.title) if kind else "")
        self.params.set_kind(kind, style.params if style else None)
        self.blend_combo.clear()
        for mode in STYLE_BLEND_MODES:
            self.blend_combo.addItem(_ts(mode.replace("_", " ").title()), mode)
        if style:
            index = self.blend_combo.findData(style.blend_mode)
            if index < 0:
                self.blend_combo.addItem(style.blend_mode, style.blend_mode)
                index = self.blend_combo.count() - 1
            self.blend_combo.setCurrentIndex(index)
        self._update_actions()
        self._syncing = False

    def _update_actions(self) -> None:
        style = self.selected_style()
        stored = style is not None and self._stack.get(style.style_id) is not None
        has_room = len(self._stack.styles) < LayerStyleStack.MAX_STYLES
        self.params.setEnabled(stored or has_room)
        self.blend_combo.setEnabled(style is not None and (stored or has_room))
        self.remove_button.setEnabled(stored)
        self.up_button.setEnabled(stored)
        self.down_button.setEnabled(stored)
        self.add_button.setEnabled(has_room)
        for row in range(self.list.count()):
            style_id = str(self.list.item(row).data(_ROLE))
            self.effect_switches[style_id].setEnabled(
                has_room or self._stack.get(style_id) is not None
            )

    def add_selected_kind(self) -> LayerStyleSpec | None:
        kind = self._registry.get("style", str(self.kind_combo.currentData()))
        if kind is None or len(self._stack.styles) >= LayerStyleStack.MAX_STYLES:
            return None
        style = LayerStyleSpec(f"style-{uuid.uuid4().hex[:8]}", kind.kind_id, kind.defaults())
        self._stack = self._stack.with_style(style)
        self._refresh(style.style_id)
        self._queue_update()
        return style

    def remove_selected(self) -> None:
        style = self.selected_style()
        if style is not None and self._stack.get(style.style_id) is not None:
            self._stack = self._stack.without(style.style_id)
            self._drafts.pop(style.style_id, None)
            self._refresh()
            self._queue_update()

    def move_selected(self, delta: int) -> None:
        style = self.selected_style()
        if style is not None:
            self._stack = self._stack.moved(style.style_id, delta)
            self._refresh(style.style_id)
            self._queue_update()

    def _on_enabled(self, style_id: str, checked: bool) -> None:
        if self._syncing:
            return
        self.select_style(style_id)
        style = self.selected_style()
        if style is None or style.enabled == checked:
            return
        self._stack = self._stack.with_style(style.with_enabled(checked))
        self._update_actions()
        self._queue_update()

    def _on_params_changed(self) -> None:
        style = self.selected_style()
        if self._syncing or style is None:
            return
        params, _issues = self._registry.validate("style", style.kind, self.params.values())
        self._stack = self._stack.with_style(style.with_params(params))
        self._update_actions()
        self._queue_update()

    def _on_blend_changed(self) -> None:
        style = self.selected_style()
        if self._syncing or style is None:
            return
        self._stack = self._stack.with_style(
            replace(style, blend_mode=self.blend_combo.currentData())
        )
        self._update_actions()
        self._queue_update()

    def _apply_preset(self, stack: LayerStyleStack) -> None:
        self._stack = stack
        self._drafts.clear()
        self._refresh()
        self._queue_update()

    def _queue_update(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def apply(self) -> bool:
        self._timer.stop()
        if self._stack == self._last_applied:
            return True
        ok = bool(self._on_apply(self._stack))
        if ok:
            self._last_applied = self._stack
        else:
            self._stack = self._last_applied
            self._refresh()
        self._status_source = "" if ok else "Could not apply styles."
        self.status_label.setText(_ts(self._status_source))
        return ok

    def _publish_live(self) -> None:
        if self._stack == self._last_applied:
            return
        if self._can_apply():
            self.apply()
        else:
            # Let an asynchronous CMYK frame finish before replacing its
            # structure. Keep only the latest input while the worker is busy.
            self._timer.start()

    def done(self, result: int) -> None:
        if self.apply():
            super().done(result)

    def rasterize(self) -> str | None:
        if self._on_rasterize is None or not self.apply():
            return None
        new_id = self._on_rasterize()
        self._status_source = "Rasterized into a new layer." if new_id else "Nothing to rasterize."
        self.status_label.setText(_ts(self._status_source))
        return new_id

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "params"):
            self.setWindowTitle(_ts("Layer Styles"))
            for button, source in self._buttons:
                button.setText(_ts(source))
            for index, kind in enumerate(self._registry.kinds("style")):
                self.kind_combo.setItemText(index, _ts(kind.title))
            self.status_label.setText(_ts(self._status_source))
            self.left_tabs.setTabText(0, _ts("Effects"))
            self.left_tabs.setTabText(1, _ts("Presets"))
            self.presets.retranslate()
            self.blend_label.setText(_ts("Blend mode"))
            self.blend_combo.setAccessibleName(_ts("Blend mode"))
            self._refresh()
