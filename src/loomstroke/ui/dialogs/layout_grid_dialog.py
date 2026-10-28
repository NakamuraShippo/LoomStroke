"""Grid display and snapping preferences; no document editing."""

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
    QSpinBox,
    QWidget,
)

from ...core.layout_grid_model import GRID_KINDS
from ..translations import translate_with_fallback

_KIND_NAMES = (
    "Square",
    "Major and minor lines",
    "Separate X/Y spacing",
    "Isometric",
    "Triangular",
    "Hexagonal",
    "Polar",
    "Baseline",
)


class LayoutGridDialog(QDialog):
    def __init__(self, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        self.labels = []
        self.kind = QComboBox(self)
        for key in GRID_KINDS:
            self.kind.addItem(key, key)
        self.kind.setCurrentIndex(
            max(0, self.kind.findData(settings.get("view/grid_kind", "square")))
        )
        self._row(form, "Grid type", self.kind)
        self.spacing = self._spin(1, 5000, settings.get("view/grid_size", 32))
        self.y_spacing = QDoubleSpinBox(self)
        self.y_spacing.setRange(1, 5000)
        self.y_spacing.setValue(float(settings.get("view/grid_y_size", 32)))
        self._row(form, "Horizontal spacing", self.spacing)
        self._row(form, "Vertical spacing", self.y_spacing)
        self.major = self._spin(1, 1000, settings.get("view/grid_major_every", 5))
        self._row(form, "Major line every", self.major)
        self.rotation = QDoubleSpinBox(self)
        self.rotation.setRange(-360, 360)
        self.rotation.setValue(float(settings.get("view/grid_rotation", 0.0)))
        self._row(form, "Rotation", self.rotation)
        self.polar = self._spin(3, 360, settings.get("view/grid_polar_divisions", 12))
        self._row(form, "Polar divisions", self.polar)
        self.baseline_spacing = QDoubleSpinBox(self)
        self.baseline_spacing.setRange(0.1, 5000)
        self.baseline_spacing.setValue(float(settings.get("view/grid_baseline_spacing", 20.0)))
        self._row(form, "Line spacing", self.baseline_spacing)
        self.baseline_start = QDoubleSpinBox(self)
        self.baseline_start.setRange(-1000000, 1000000)
        self.baseline_start.setValue(float(settings.get("view/grid_baseline_start", 0.0)))
        self._row(form, "Start position", self.baseline_start)
        self.baseline_unit = QComboBox(self)
        for unit in ("px", "mm"):
            self.baseline_unit.addItem(unit, unit)
        self.baseline_unit.setCurrentIndex(
            max(0, self.baseline_unit.findData(settings.get("view/grid_baseline_unit", "px")))
        )
        self._row(form, "Baseline unit", self.baseline_unit)
        self.checks = {}
        for source, key, default in (
            ("Follow ruler origin", "view/grid_follow_ruler_origin", False),
            ("Show grid", "view/grid_visible", False),
            ("Snap to grid", "view/grid_snap", False),
        ):
            check = QCheckBox(self)
            check.setChecked(bool(settings.get(key, default)))
            self.checks[key] = (check, source)
            form.addRow(check)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        form.addRow(self.note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(self._update_kind)
        self._update_kind()
        self._retranslate()

    def _update_kind(self) -> None:
        self.y_spacing.setEnabled(self.kind.currentData() == "rectangular")
        self.major.setEnabled(self.kind.currentData() == "major")
        self.polar.setEnabled(self.kind.currentData() == "polar")
        for widget in (self.baseline_spacing, self.baseline_start, self.baseline_unit):
            widget.setEnabled(self.kind.currentData() == "baseline")

    def _spin(self, low: int, high: int, value: int) -> QSpinBox:
        spin = QSpinBox(self)
        spin.setRange(low, high)
        spin.setValue(int(value))
        return spin

    def _row(self, form: QFormLayout, source: str, widget: QWidget) -> None:
        label = QLabel(self)
        self.labels.append((label, source))
        form.addRow(label, widget)

    def values(self) -> dict[str, Any]:
        values = {
            "view/grid_kind": self.kind.currentData(),
            "view/grid_size": self.spacing.value(),
            "view/grid_y_size": self.y_spacing.value(),
            "view/grid_major_every": self.major.value(),
            "view/grid_rotation": self.rotation.value(),
            "view/grid_polar_divisions": self.polar.value(),
            "view/grid_baseline_spacing": self.baseline_spacing.value(),
            "view/grid_baseline_start": self.baseline_start.value(),
            "view/grid_baseline_unit": self.baseline_unit.currentData(),
        }
        values.update({key: check.isChecked() for key, (check, _source) in self.checks.items()})
        return values

    def _retranslate(self) -> None:
        def ts(source: str) -> str:
            return translate_with_fallback("LayoutGridDialog", source)

        self.setWindowTitle(ts("Grid Settings"))
        for label, source in self.labels:
            label.setText(ts(source))
        for check, source in self.checks.values():
            check.setText(ts(source))
        for index, source in enumerate(_KIND_NAMES):
            self.kind.setItemText(index, ts(source))
        self.note.setText(ts("Dense grids are visually thinned; snapping uses the full grid."))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def open_layout_grid(window: Any) -> None:
    dialog = LayoutGridDialog(window._settings, window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    values = dialog.values()
    for key, value in values.items():
        window._settings.set(key, value)
    canvas = getattr(window, "canvas", None)
    if canvas is not None:
        canvas.state.grid_visible = values["view/grid_visible"]
        canvas.set_grid_size(values["view/grid_size"])
        canvas.update()
