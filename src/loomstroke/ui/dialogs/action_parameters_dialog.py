"""Generic parameter / target form generated from an ``ActionDescriptor`` (CT-20 §6)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.actions import ActionDescriptor, ActionParameter, TargetSelector
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["ActionParametersDialog"]


def _ts(text: str) -> str:
    return translated_literal_domain("ActionsPanel", text, use_fallback=True)


class ActionParametersDialog(QDialog):
    _LAYER_SELECTORS = ("active_layer", "all_layers", "layer_by_name")

    def __init__(
        self,
        descriptor: ActionDescriptor,
        parent: QWidget | None = None,
        *,
        initial: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self._descriptor = descriptor
        self._editors: dict[str, QWidget] = {}
        self.setWindowTitle(descriptor.title)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        initial = dict(initial or {})
        for parameter in descriptor.parameters:
            editor = self._editor_for(parameter, initial.get(parameter.name, parameter.default))
            self._editors[parameter.name] = editor
            form.addRow(parameter.name, editor)
        self.selector_combo: QComboBox | None = None
        self.selector_value = QLineEdit()
        if descriptor.target_kind == "layer":
            self.selector_combo = QComboBox()
            for kind in self._LAYER_SELECTORS:
                self.selector_combo.addItem(_ts(kind), kind)
            form.addRow(_ts("Target"), self.selector_combo)
            self.selector_value.setPlaceholderText(_ts("layer name (for layer_by_name)"))
            form.addRow(_ts("Layer name"), self.selector_value)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- editors -----------------------------------------------------------------------

    def _editor_for(self, parameter: ActionParameter, value: Any) -> QWidget:
        if parameter.kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            return box
        if parameter.kind == "int":
            spin = QSpinBox()
            spin.setRange(int(parameter.minimum or -1_000_000), int(parameter.maximum or 1_000_000))
            spin.setValue(int(value or 0))
            return spin
        if parameter.kind == "float":
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setSingleStep(0.05)
            spin.setRange(float(parameter.minimum or -1e9), float(parameter.maximum or 1e9))
            spin.setValue(float(value or 0.0))
            return spin
        if parameter.kind == "enum":
            combo = QComboBox()
            for choice in parameter.choices:
                combo.addItem(choice, choice)
            if value in parameter.choices:
                combo.setCurrentIndex(parameter.choices.index(value))
            return combo
        edit = QLineEdit(str(value) if value is not None else "")
        if parameter.kind != "path":
            return edit
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        browse = QPushButton(_ts("Browse..."))
        browse.clicked.connect(lambda: self._browse(edit))
        row.addWidget(browse)
        holder.setProperty("line_edit", edit)
        return holder

    def _browse(self, edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, _ts("Choose a folder"))
        if chosen:
            edit.setText(chosen)

    # -- results -----------------------------------------------------------------------

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, editor in self._editors.items():
            if isinstance(editor, QCheckBox):
                result[name] = editor.isChecked()
            elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
                result[name] = editor.value()
            elif isinstance(editor, QComboBox):
                result[name] = editor.currentData()
            elif isinstance(editor, QLineEdit):
                result[name] = editor.text()
            else:
                line_edit = editor.property("line_edit")
                result[name] = line_edit.text() if isinstance(line_edit, QLineEdit) else ""
        return result

    def selector(self) -> TargetSelector:
        kind = self._descriptor.target_kind
        if kind == "layer" and self.selector_combo is not None:
            chosen = str(self.selector_combo.currentData())
            value = self.selector_value.text().strip() or None
            return TargetSelector(chosen, value if chosen == "layer_by_name" else None)  # type: ignore[arg-type]
        if kind == "page":
            return TargetSelector("active_page")
        if kind == "document":
            return TargetSelector("document")
        return TargetSelector("none")

    @classmethod
    def ask(
        cls, descriptor: ActionDescriptor, parent: QWidget | None = None
    ) -> tuple[dict[str, Any], TargetSelector] | None:
        dialog = cls(descriptor, parent)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            return dialog.values(), dialog.selector()
        finally:
            dialog.deleteLater()
