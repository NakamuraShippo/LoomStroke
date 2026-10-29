"""Composition overlay preferences, independent of document editing/history."""

from __future__ import annotations

from copy import deepcopy
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
from ..canvas.guide_dialog_preview import attach_guide_preview
from ..canvas.guide_reference import guide_reference_rect
from ..translations import translate_with_fallback
from .guide_editor_window import show_guide_editor
from .guide_placement_controls import GuidePlacementControls, safe_offset

_NAMES = (
    "Rule of thirds",
    "Golden ratio",
    "Golden spiral",
    "Diagonals",
    "Center cross",
    "Safe area",
)
_REFERENCES = (
    ("Canvas", "canvas"),
    ("Trim area", "trim"),
    ("Selection", "selection"),
    ("Selected object", "object"),
)


class CompositionGuidesDialog(QDialog):
    def __init__(self, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._canvas = getattr(parent, "canvas", None)
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
        self._old_rotation = settings.get("view/composition_overlay_rotation", 0.0)
        self._old_offsets = deepcopy(settings.get("view/composition_overlay_offsets", {}))
        self._old_offsets = self._old_offsets if isinstance(self._old_offsets, dict) else {}
        first_kind = next((kind for kind in KINDS if kind in selected), None)
        initial_offset = self._old_offsets.get(first_kind, (0, 0))
        self.placement = GuidePlacementControls(
            form, self, angle=self._old_rotation, offset=initial_offset
        )
        self._initial_offset = self.placement.offset()
        self._offset_dirty = False
        self.placement.x.valueChanged.connect(self._mark_offset)
        self.placement.y.valueChanged.connect(self._mark_offset)
        self.error = QLabel(self)
        self._error_source = ""
        form.addRow(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        form.addRow(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._retranslate()
        self._guide_preview = attach_guide_preview(self, parent, "composition", self.values)

    def values(self) -> dict[str, Any]:
        values = {
            "view/composition_overlay_kinds": [
                k for k, check in self.checks.items() if check.isChecked()
            ],
            "view/composition_overlay_reference": self.reference.currentData(),
            "view/composition_overlay_safe_percent": self.percent.value(),
        }
        if self.placement.angle.value() != 0 or self.placement.angle.value() != self._old_rotation:
            values["view/composition_overlay_rotation"] = self.placement.angle.value()
        if self._offset_dirty:
            offsets = deepcopy(self._old_offsets)
            for kind in values["view/composition_overlay_kinds"]:
                base = safe_offset(self._old_offsets.get(kind, (0, 0)))
                offsets[kind] = [
                    base[i] + self.placement.offset()[i] - self._initial_offset[i] for i in (0, 1)
                ]
            values["view/composition_overlay_offsets"] = offsets
        return values

    def _mark_offset(self, *_args):
        self._offset_dirty = True

    def accept(self):
        if (
            self._canvas is not None
            and guide_reference_rect(self._canvas, self.reference.currentData()) is None
        ):
            self._error_source = "Select an object or choose another reference area."
            self._retranslate()
            return
        super().accept()

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
        self.placement.retranslate()
        self.error.setText(
            translate_with_fallback("GuidePlacement", self._error_source)
            if self._error_source
            else ""
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def open_composition_guides(window: Any) -> CompositionGuidesDialog:
    dialog = CompositionGuidesDialog(window._settings, window)

    def commit():
        for key, value in dialog.values().items():
            window._settings.set(key, value)
        canvas = getattr(window, "canvas", None)
        if canvas is not None:
            canvas.update()

    return show_guide_editor(dialog, window.canvas, commit)
