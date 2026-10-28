"""Composition overlay preferences, independent of document editing/history."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QWidget,
)

from ...core.composition_geometry import KINDS
from ..translations import translate_with_fallback

_NAMES = (
    "Rule of thirds",
    "Golden ratio",
    "Golden spiral",
    "Diagonals",
    "Center cross",
    "Safe area",
)
_REFERENCES = (("Canvas", "canvas"), ("Trim area", "trim"), ("Selection", "selection"))


class CompositionGuidesDialog(QDialog):
    def __init__(self, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        selected = settings.get("view/composition_overlay_kinds", [])
        selected = selected if isinstance(selected, (list, tuple)) else []
        self.checks = {}
        for kind in KINDS:
            check = QCheckBox(self)
            check.setChecked(kind in selected)
            form.addRow(check)
            self.checks[kind] = check
        self.reference = QComboBox(self)
        for source, value in _REFERENCES:
            self.reference.addItem(source, value)
        self.reference.setCurrentIndex(
            max(
                0,
                self.reference.findData(
                    settings.get("view/composition_overlay_reference", "canvas")
                ),
            )
        )
        self.reference_label = QLabel(self)
        form.addRow(self.reference_label, self.reference)
        self.percent = QDoubleSpinBox(self)
        self.percent.setRange(0, 49)
        self.percent.setSuffix(" %")
        self.percent.setValue(float(settings.get("view/composition_overlay_safe_percent", 10.0)))
        self.percent_label = QLabel(self)
        form.addRow(self.percent_label, self.percent)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        form.addRow(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._retranslate()

    def values(self) -> dict[str, Any]:
        return {
            "view/composition_overlay_kinds": [
                k for k, check in self.checks.items() if check.isChecked()
            ],
            "view/composition_overlay_reference": self.reference.currentData(),
            "view/composition_overlay_safe_percent": self.percent.value(),
        }

    def _retranslate(self) -> None:
        def ts(source: str) -> str:
            return translate_with_fallback("CompositionGuidesDialog", source)

        self.setWindowTitle(ts("Composition Guides"))
        for kind, source in zip(KINDS, _NAMES, strict=True):
            self.checks[kind].setText(ts(source))
        for i, (source, _value) in enumerate(_REFERENCES):
            self.reference.setItemText(i, ts(source))
        self.reference_label.setText(ts("Reference area"))
        self.percent_label.setText(ts("Safe area margin"))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def open_composition_guides(window: Any) -> None:
    dialog = CompositionGuidesDialog(window._settings, window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    for key, value in dialog.values().items():
        window._settings.set(key, value)
    canvas = getattr(window, "canvas", None)
    if canvas is not None:
        canvas.update()
