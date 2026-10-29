"""Numeric guide entry; callers own page-state persistence."""

from __future__ import annotations

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

from ...core.guide_layout_geometry import (
    MAX_DIVISIONS,
    GuidePosition,
    batch_guides,
    modular_guides,
    numeric_guide,
    unit_to_pixels,
)
from ..canvas.guide_dialog_preview import attach_guide_preview
from ..canvas.guide_reference import guide_reference_rect
from ..translations import translate_with_fallback
from .guide_placement_controls import GuidePlacementControls


class GuideLayoutDialog(QDialog):
    """Build a validated guide list, without modifying the document."""

    def __init__(
        self,
        *,
        size: tuple[int, int],
        dpi: tuple[float, float],
        unit: str = "px",
        origin: tuple[float, float] = (0.0, 0.0),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._size, self._dpi, self._origin = size, dpi, origin
        self._canvas = getattr(parent, "_canvas", None) or getattr(parent, "canvas", None)
        self._labels: list[tuple[QLabel, str]] = []
        self._checks: list[tuple[QCheckBox, str]] = []
        form = QFormLayout(self)
        self.mode = QComboBox(self)
        self.mode.addItem("", "single")
        self.mode.addItem("", "batch")
        self.mode.addItem("", "modular")
        self._row(form, "Mode", self.mode)
        self.orientation = QComboBox(self)
        self.orientation.addItem("", "vertical")
        self.orientation.addItem("", "horizontal")
        self._row(form, "Orientation", self.orientation)
        self.unit = QComboBox(self)
        for value in ("px", "mm", "inch"):
            self.unit.addItem(value, value)
        self.unit.setCurrentIndex(max(0, self.unit.findData(unit)))
        self._row(form, "Unit", self.unit)
        self.position = self._distance()
        self._row(form, "Position", self.position)
        self.columns, self.rows = QSpinBox(self), QSpinBox(self)
        for spin in (self.columns, self.rows):
            spin.setRange(1, MAX_DIVISIONS)
        self._row(form, "Columns", self.columns)
        self._row(form, "Rows", self.rows)
        self.column_gutter, self.row_gutter = self._distance(), self._distance()
        self._row(form, "Column gutter", self.column_gutter)
        self._row(form, "Row gutter", self.row_gutter)
        self.use_margins = self._check(form, "Create margin guides")
        self.margins = [self._distance() for _ in range(4)]
        for source, spin in zip(("Left", "Top", "Right", "Bottom"), self.margins, strict=True):
            self._row(form, source, spin)
        self.center_vertical = self._check(form, "Vertical center")
        self.center_horizontal = self._check(form, "Horizontal center")
        self.replace = self._check(form, "Replace existing guides")
        self.reference = QComboBox(self)
        for label, value in (
            ("Canvas", "canvas"),
            ("Selection", "selection"),
            ("Selected object", "object"),
        ):
            self.reference.addItem(label, value)
        self._row(form, "Reference area", self.reference)
        self.placement = GuidePlacementControls(form, self)
        self.error = QLabel(self)
        self._error_source = ""
        form.addRow(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        form.addRow(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.mode.currentIndexChanged.connect(self._update_mode)
        self._retranslate()
        self._update_mode()
        self._guide_preview = attach_guide_preview(
            self, parent, "layout", lambda: (self.guide_positions(), self.replace.isChecked())
        )

    def _distance(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(0, 1_000_000_000)
        spin.setDecimals(6)
        return spin

    def _row(self, form: QFormLayout, source: str, widget: QWidget) -> None:
        label = QLabel(self)
        self._labels.append((label, source))
        form.addRow(label, widget)

    def _check(self, form: QFormLayout, source: str) -> QCheckBox:
        widget = QCheckBox(self)
        self._checks.append((widget, source))
        form.addRow(widget)
        return widget

    def _update_mode(self) -> None:
        single = self.mode.currentData() == "single"
        for widget in (self.orientation, self.position):
            widget.setEnabled(single)
        for widget in (self.column_gutter, self.row_gutter):
            widget.setEnabled(self.mode.currentData() == "modular")
        for widget in (
            self.columns,
            self.rows,
            self.use_margins,
            *self.margins,
            self.center_vertical,
            self.center_horizontal,
        ):
            widget.setEnabled(not single)
        if self.mode.currentData() == "modular":
            self.center_vertical.setEnabled(False)
            self.center_horizontal.setEnabled(False)
        self.error.clear()

    def _reference_rect(self):
        if self._canvas is None:
            return (0, 0, *self._size)
        rect = guide_reference_rect(self._canvas, self.reference.currentData())
        if rect is None:
            raise ValueError("No reference frame")
        return rect

    def placement_options(self):
        rect = self._reference_rect()
        dx, dy = self.placement.offset()
        angle = self.placement.angle.value()
        return (
            {}
            if not angle
            else {
                "rotation": angle,
                "pivot": (rect[0] + rect[2] / 2 + dx, rect[1] + rect[3] / 2 + dy),
            }
        )

    def guide_positions(self) -> list[GuidePosition]:
        rect = self._reference_rect()
        positions = self._local_positions((rect[2], rect[3]))
        dx, dy = self.placement.offset()
        positions = [
            (axis, position + (rect[0] + dx if axis == "vertical" else rect[1] + dy))
            for axis, position in positions
        ]
        # Keep legacy coordinate validation before any persistent write.
        return [
            numeric_guide(axis, position, unit="px", dpi=self._dpi, size=self._size)
            for axis, position in positions
        ]

    def _local_positions(self, size) -> list[GuidePosition]:
        unit = self.unit.currentData()
        if self.mode.currentData() == "single":
            return [
                numeric_guide(
                    self.orientation.currentData(),
                    self.position.value(),
                    unit=unit,
                    dpi=self._dpi,
                    size=size,
                    origin=self._origin if self.reference.currentData() == "canvas" else (0, 0),
                )
            ]
        margins = None
        if self.use_margins.isChecked():
            margins = tuple(
                unit_to_pixels(spin.value(), unit, self._dpi[i % 2])
                for i, spin in enumerate(self.margins)
            )
        if self.mode.currentData() == "modular":
            return modular_guides(
                size,
                columns=self.columns.value(),
                rows=self.rows.value(),
                margins=margins or (0.0, 0.0, 0.0, 0.0),
                column_gutter=unit_to_pixels(self.column_gutter.value(), unit, self._dpi[0]),
                row_gutter=unit_to_pixels(self.row_gutter.value(), unit, self._dpi[1]),
            )
        return batch_guides(
            size,
            columns=self.columns.value(),
            rows=self.rows.value(),
            margins=margins,
            center_vertical=self.center_vertical.isChecked(),
            center_horizontal=self.center_horizontal.isChecked(),
        )

    def accept(self) -> None:
        controller = getattr(self._canvas, "_guide_controller", None)
        if (
            controller is not None
            and getattr(controller, "guides_locked", False)
            and self.replace.isChecked()
        ):
            self._error_source = "Unlock guides before replacing them."
            self.error.setText(self._ts(self._error_source))
            return
        try:
            self._reference_rect()
        except ValueError:
            self._error_source = "The reference area is unavailable."
            self.error.setText(self._ts(self._error_source))
            return
        try:
            self.guide_positions()
        except ValueError:
            self._error_source = "Guides must be inside the canvas with valid margins."
            self.error.setText(self._ts(self._error_source))
            return
        super().accept()

    @staticmethod
    def _ts(source: str) -> str:
        return translate_with_fallback("GuideLayoutDialog", source)

    def _retranslate(self) -> None:
        self.setWindowTitle(self._ts("Create Guides"))
        for index, source in enumerate(("Canvas", "Selection", "Selected object")):
            self.reference.setItemText(index, self._ts(source))
        self.placement.retranslate()
        for label, source in self._labels:
            label.setText(self._ts(source))
        for check, source in self._checks:
            check.setText(self._ts(source))
        for combo, sources in (
            (self.mode, ("Single guide", "Batch guides", "Modular grid")),
            (self.orientation, ("Vertical", "Horizontal")),
        ):
            for i, source in enumerate(sources):
                combo.setItemText(i, self._ts(source))
        if self.error.text():
            self.error.setText(
                self._ts(
                    self._error_source or "Guides must be inside the canvas with valid margins."
                )
            )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()
