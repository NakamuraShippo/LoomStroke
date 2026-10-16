"""``Fill Layer...`` dialog: pick a fill kind and its parameters (CT-21 §6)."""

from __future__ import annotations

import uuid
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.appearance import AppearanceRegistry, FillSpec
from loomstroke.ui.translations import translated_literal_domain

from .appearance_param_form import AppearanceParamForm

__all__ = ["FillLayerDialog"]


def _ts(text: str) -> str:
    return translated_literal_domain("AppearancePanel", text, use_fallback=True)


class FillLayerDialog(QDialog):
    def __init__(
        self,
        registry: AppearanceRegistry,
        *,
        initial: FillSpec | None = None,
        parent: QWidget | None = None,
        color_picker: Any = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._initial = initial
        self.setWindowTitle(_ts("Fill Layer") if initial is None else _ts("Edit Fill"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind_combo = QComboBox()
        for kind in registry.kinds("fill"):
            self.kind_combo.addItem(_ts(kind.title), kind.kind_id)
        form.addRow(_ts("Kind"), self.kind_combo)
        layout.addLayout(form)
        self.params = AppearanceParamForm(None, self, color_picker=color_picker)
        layout.addWidget(self.params)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.kind_combo.currentIndexChanged.connect(lambda _i: self._sync_kind())
        if initial is not None:
            index = self.kind_combo.findData(initial.kind)
            if index >= 0:
                self.kind_combo.setCurrentIndex(index)
        self._sync_kind(initial.params if initial is not None else None)

    def _sync_kind(self, values: Any = None) -> None:
        kind = self._registry.get("fill", str(self.kind_combo.currentData()))
        self.params.set_kind(kind, values)

    def spec(self) -> FillSpec:
        kind_id = str(self.kind_combo.currentData())
        params, _issues = self._registry.validate("fill", kind_id, self.params.values())
        if self._initial is not None and self._initial.kind == kind_id:
            return self._initial.with_params(params)
        fill_id = (
            self._initial.fill_id if self._initial is not None else f"fill-{uuid.uuid4().hex[:8]}"
        )
        return FillSpec(fill_id, kind_id, params)

    @classmethod
    def ask(
        cls,
        registry: AppearanceRegistry,
        *,
        initial: FillSpec | None = None,
        parent: QWidget | None = None,
    ) -> FillSpec | None:
        dialog = cls(registry, initial=initial, parent=parent)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            return dialog.spec()
        finally:
            dialog.deleteLater()
