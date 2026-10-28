"""Drawing Assist dock: list of assists per page, add / remove / activate, numeric editing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.drawing_assist import KNOWN_ASSIST_TYPES, DrawingAssist
from loomstroke.ui.canvas.drawing_assist_controller import DrawingAssistController
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["DrawingAssistPanel"]

_ROLE = 0x0100  # Qt.UserRole
_ELLIPSE_LABELS = {
    "center.x": "Center X",
    "center.y": "Center Y",
    "major_diameter": "Major diameter",
    "minor_diameter": "Minor diameter",
    "angle": "Rotation",
    "concentric": "Concentric (0 or 1)",
}


def _ts(text: str) -> str:
    return translated_literal_domain("DrawingAssistPanel", text, use_fallback=True)


class DrawingAssistPanel(QWidget):
    def __init__(self, controller: DrawingAssistController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._fields: dict[tuple[str, str, int | None], QDoubleSpinBox] = {}
        self._editing: DrawingAssist | None = None
        self._build()
        self.refresh()

    # -- build -------------------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.type_combo = QComboBox()
        for kind in KNOWN_ASSIST_TYPES:
            self.type_combo.addItem(_ts(kind), kind)
        row.addWidget(self.type_combo, 1)
        self.add_button = self._button(row, "Add", self.add_selected_type)
        self.remove_button = self._button(row, "Remove", self.remove_selected)
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.list, 1)
        row2 = QHBoxLayout()
        self.activate_button = self._button(row2, "Set Active", self.activate_selected)
        self.enable_box = QCheckBox(_ts("Enabled"))
        self.enable_box.toggled.connect(self._on_enabled_toggled)
        row2.addWidget(self.enable_box)
        self.overlay_box = QCheckBox(_ts("Show overlay"))
        self.overlay_box.setChecked(self._controller.overlay_visible)
        self.overlay_box.toggled.connect(self._on_overlay_toggled)
        row2.addWidget(self.overlay_box)
        row2.addStretch(1)
        layout.addLayout(row2)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        layout.addWidget(self.form_host)
        row3 = QHBoxLayout()
        self.apply_button = self._button(row3, "Apply", self.apply_fields)
        self.refresh_button = self._button(row3, "Refresh", self.refresh)
        row3.addStretch(1)
        layout.addLayout(row3)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _button(self, row: QHBoxLayout, text: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(_ts(text))
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    # -- data --------------------------------------------------------------------------

    def refresh(self) -> None:
        selected = self.selected_id()
        state = self._controller.state()
        self.list.clear()
        for assist in state.items:
            flags = []
            if assist.id == state.active_id:
                flags.append(_ts("active"))
            if not assist.enabled:
                flags.append(_ts("disabled"))
            if not assist.is_known:
                flags.append(_ts("unknown type"))
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            item = QListWidgetItem(f"{_ts(assist.type)}{suffix}")
            item.setData(_ROLE, assist.id)
            self.list.addItem(item)
        for row in range(self.list.count()):
            if self.list.item(row).data(_ROLE) == selected:
                self.list.setCurrentRow(row)
                break
        self._on_selection()
        self.status_label.setText(
            _ts("{count} assist(s) on this page").format(count=len(state.items))
        )

    def selected_id(self) -> str | None:
        item = self.list.currentItem()
        return str(item.data(_ROLE)) if item is not None else None

    def select(self, assist_id: str) -> bool:
        for row in range(self.list.count()):
            if self.list.item(row).data(_ROLE) == assist_id:
                self.list.setCurrentRow(row)
                return True
        return False

    # -- actions -----------------------------------------------------------------------

    def add_selected_type(self) -> DrawingAssist | None:
        assist = self._controller.add(str(self.type_combo.currentData()))
        self.refresh()
        if assist is not None:
            self.select(assist.id)
        return assist

    def remove_selected(self) -> None:
        assist_id = self.selected_id()
        if assist_id:
            self._controller.remove(assist_id)
            self.refresh()

    def activate_selected(self) -> None:
        assist_id = self.selected_id()
        if assist_id:
            self._controller.set_active(assist_id)
            self.refresh()

    def apply_fields(self) -> bool:
        assist = self._editing
        if assist is None:
            return False
        geometry: dict[str, Any] = dict(assist.geometry)
        options: dict[str, Any] = dict(assist.options)
        for (source, key, index), spin in self._fields.items():
            store = geometry if source == "geometry" else options
            value = spin.value()
            if index is None:
                store[key] = int(value) if key == "count" else value
                continue
            current = list(store.get(key, []))
            if isinstance(current[index // 2], (list, tuple)):
                point = list(current[index // 2])
                point[index % 2] = value
                current[index // 2] = point
            else:
                current[index] = value
            store[key] = current
        updated = DrawingAssist(
            assist.id, assist.type, assist.enabled, assist.locked, geometry, options
        )
        issues = self._controller.issues(updated)
        if issues:
            self.status_label.setText(
                _ts("Not applied: {codes}").format(codes=", ".join(i.code for i in issues))
            )
            return False
        ok = self._controller.update(updated)
        self.refresh()
        return ok

    # -- internals ---------------------------------------------------------------------

    def _on_selection(self) -> None:
        assist = self._controller.state().get(self.selected_id())
        self._editing = assist
        self.enable_box.blockSignals(True)
        self.enable_box.setChecked(bool(assist and assist.enabled))
        self.enable_box.blockSignals(False)
        self._rebuild_form(assist)

    def _on_enabled_toggled(self, checked: bool) -> None:
        assist = self._editing
        if assist is None or assist.enabled == checked:
            return
        self._controller.update(
            DrawingAssist(
                assist.id, assist.type, checked, assist.locked, assist.geometry, assist.options
            )
        )
        self.refresh()

    def _on_overlay_toggled(self, checked: bool) -> None:
        self._controller.overlay_visible = bool(checked)
        self._controller._repaint()  # noqa: SLF001 - same component

    def _rebuild_form(self, assist: DrawingAssist | None) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._fields.clear()
        if assist is None or not assist.is_known:
            return
        for source, mapping in (("geometry", assist.geometry), ("options", assist.options)):
            for key, value in mapping.items():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    self._add_field(source, key, None, float(value), f"{key}")
                elif (
                    isinstance(value, (list, tuple))
                    and len(value) == 2
                    and all(isinstance(v, (int, float)) for v in value)
                ):
                    self._add_field(source, key, 0, float(value[0]), f"{key}.x")
                    self._add_field(source, key, 1, float(value[1]), f"{key}.y")
                elif isinstance(value, (list, tuple)):
                    for index, point in enumerate(value):
                        if isinstance(point, (list, tuple)) and len(point) == 2:
                            self._add_field(
                                source, key, index * 2, float(point[0]), f"{key}[{index}].x"
                            )
                            self._add_field(
                                source, key, index * 2 + 1, float(point[1]), f"{key}[{index}].y"
                            )

    def _add_field(
        self, source: str, key: str, index: int | None, value: float, label: str
    ) -> None:
        spin = QDoubleSpinBox()
        spin.setRange(-1e6, 1e6)
        if self._editing is not None and self._editing.type == "ellipse" and key == "concentric":
            spin.setRange(0, 1)
        spin.setDecimals(0 if key == "count" else 2)
        if self._editing is not None and self._editing.type == "ellipse" and key == "concentric":
            spin.setDecimals(0)
        spin.setValue(value)
        if self._editing is not None and self._editing.type == "ellipse":
            label = _ts(_ELLIPSE_LABELS.get(label, label))
        self.form.addRow(label, spin)
        self._fields[(source, key, index)] = spin

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() != QEvent.Type.LanguageChange or not hasattr(self, "type_combo"):
            return
        index = self.type_combo.findData("ellipse")
        if index >= 0:
            self.type_combo.setItemText(index, _ts("ellipse"))
        state = self._controller.state()
        for row in range(self.list.count()):
            item = self.list.item(row)
            assist = state.get(str(item.data(_ROLE)))
            if assist is not None and assist.type == "ellipse":
                flags = []
                if assist.id == state.active_id:
                    flags.append(_ts("active"))
                if not assist.enabled:
                    flags.append(_ts("disabled"))
                suffix = f"  [{', '.join(flags)}]" if flags else ""
                item.setText(f"{_ts('ellipse')}{suffix}")
        if self._editing is not None and self._editing.type == "ellipse":
            for (_source, key, index), spin in self._fields.items():
                source = f"{key}.{'x' if index == 0 else 'y'}" if index is not None else key
                label = self.form.labelForField(spin)
                if isinstance(label, QLabel):
                    label.setText(_ts(_ELLIPSE_LABELS.get(source, source)))
