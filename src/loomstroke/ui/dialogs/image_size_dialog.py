"""Image Size dialog (CT-13 R1): pixel / physical / DPI linkage, optional resample.

Photoshop-style semantics, all computed by :class:`ImageSizePlanner` so the
dialog only wires widgets:

- Resample ON: pixel edits change the physical size (DPI fixed); DPI edits
  change the pixel size (physical size fixed); physical edits change the
  pixel size (DPI fixed).
- Resample OFF: the pixel size is locked to the document; DPI and physical
  size trade off against each other.

Stable values (interpolation ids, unit ids) are kept apart from their
translated labels so a runtime language switch never changes the result.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.image_size.image_size_planner import ImageSizePlanner
from ...core.image_size.image_size_target import ImageSizeTarget
from ...core.image_size.interpolation import InterpolationMode
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from ..widgets.spinbox_sizing import apply_dimension_spinbox_policy, fit_spinbox_to_values

_INTERPOLATION_LABELS: dict[InterpolationMode, str] = {
    InterpolationMode.NEAREST: "Nearest Neighbor",
    InterpolationMode.BILINEAR: "Bilinear",
    InterpolationMode.BICUBIC: "Bicubic (recommended)",
    InterpolationMode.LANCZOS: "Lanczos (best for reduction)",
    InterpolationMode.HAMMING: "Hamming",
    InterpolationMode.BOX: "Box (integer reduction)",
}
#: (stable unit id, inches -> unit factor, source label)
_UNITS: tuple[tuple[str, float, str], ...] = (
    ("mm", ImageSizePlanner.MM_PER_INCH, "mm"),
    ("inch", 1.0, "inch"),
)
_MAX_DPI = 1200


class ImageSizeDialog(QDialog):
    """Collect an :class:`ImageSizeTarget` for the active canvas."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        dpi: int,
        layer_count: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._planner = ImageSizePlanner()
        self._source_width = max(1, int(width))
        self._source_height = max(1, int(height))
        self._source_dpi = max(1, min(_MAX_DPI, int(dpi)))
        self._layer_count = max(1, int(layer_count))
        self._last_dpi = self._source_dpi
        self._updating = False

        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(self.setWindowTitle, self._ts("Image Size"))

        self._build_widgets()
        self._build_layout()
        self._connect_signals()
        self._refresh_derived()

    # -- public -----------------------------------------------------------------

    def target(self) -> ImageSizeTarget:
        resample = self._resample_check.isChecked()
        width = int(self._width_spin.value()) if resample else self._source_width
        height = int(self._height_spin.value()) if resample else self._source_height
        mode = InterpolationMode.normalize(self._interpolation_combo.currentData())
        return ImageSizeTarget(
            width_px=width,
            height_px=height,
            dpi=int(self._dpi_spin.value()),
            interpolation=mode or InterpolationMode.default(),
            resample=resample,
        )

    def has_changes(self) -> bool:
        target = self.target()
        return (target.width_px, target.height_px, target.dpi) != (
            self._source_width,
            self._source_height,
            self._source_dpi,
        )

    # -- construction -----------------------------------------------------------

    def _build_widgets(self) -> None:
        self._width_spin = QSpinBox(self)
        self._width_spin.setRange(ImageSizePlanner.MIN_DIMENSION, ImageSizePlanner.MAX_DIMENSION)
        self._width_spin.setValue(self._source_width)
        self._height_spin = QSpinBox(self)
        self._height_spin.setRange(ImageSizePlanner.MIN_DIMENSION, ImageSizePlanner.MAX_DIMENSION)
        self._height_spin.setValue(self._source_height)
        for spin, value in (
            (self._width_spin, self._source_width),
            (self._height_spin, self._source_height),
        ):
            suffix = self._ts(" px")
            spin.setSuffix(suffix)
            self._translation_binder.register(spin.setSuffix, suffix)
            apply_dimension_spinbox_policy(
                spin,
                (ImageSizePlanner.MIN_DIMENSION, ImageSizePlanner.MAX_DIMENSION, value),
            )

        self._percent_spin = QDoubleSpinBox(self)
        self._percent_spin.setDecimals(2)
        self._percent_spin.setRange(0.01, 10000.0)
        self._percent_spin.setValue(100.0)
        percent_suffix = self._ts(" %")
        self._percent_spin.setSuffix(percent_suffix)
        self._translation_binder.register(self._percent_spin.setSuffix, percent_suffix)

        self._physical_width_spin = QDoubleSpinBox(self)
        self._physical_height_spin = QDoubleSpinBox(self)
        for spin in (self._physical_width_spin, self._physical_height_spin):
            spin.setDecimals(2)
            spin.setRange(0.01, 1_000_000.0)

        self._unit_combo = QComboBox(self)
        for unit_id, _factor, label in _UNITS:
            self._unit_combo.addItem(self._translate_runtime(label), unit_id)

        self._dpi_spin = QSpinBox(self)
        self._dpi_spin.setRange(1, _MAX_DPI)
        self._dpi_spin.setValue(self._source_dpi)
        dpi_suffix = self._ts(" dpi")
        self._dpi_spin.setSuffix(dpi_suffix)
        self._translation_binder.register(self._dpi_spin.setSuffix, dpi_suffix)
        fit_spinbox_to_values(self._dpi_spin, (1, _MAX_DPI))

        self._constrain_check = QCheckBox(self)
        self._constrain_check.setChecked(True)
        self._translation_binder.register(
            self._constrain_check.setText, self._ts("Constrain proportions")
        )
        self._resample_check = QCheckBox(self)
        self._resample_check.setChecked(True)
        self._translation_binder.register(self._resample_check.setText, self._ts("Resample image"))

        self._interpolation_combo = QComboBox(self)
        for mode in InterpolationMode:
            self._interpolation_combo.addItem(
                self._translate_runtime(_INTERPOLATION_LABELS[mode]), mode.value
            )
        default_index = self._interpolation_combo.findData(InterpolationMode.default().value)
        if default_index >= 0:
            self._interpolation_combo.setCurrentIndex(default_index)

        self._source_label = QLabel(self)
        self._estimate_label = QLabel(self)
        for label in (self._source_label, self._estimate_label):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        for spin in (
            self._width_spin,
            self._height_spin,
            self._percent_spin,
            self._physical_width_spin,
            self._physical_height_spin,
            self._dpi_spin,
        ):
            # Commit on Enter / focus-out / step so partially typed numbers
            # never trigger the linkage and rewrite the field under the user.
            spin.setKeyboardTracking(False)

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        layout.addWidget(self._source_label)

        pixel_group = QGroupBox(self)
        self._translation_binder.register(pixel_group.setTitle, self._ts("Pixel Dimensions"))
        pixel_form = self._form(pixel_group)
        pixel_form.addRow(self._label("Width:", pixel_group), self._width_spin)
        pixel_form.addRow(self._label("Height:", pixel_group), self._height_spin)
        pixel_form.addRow(self._label("Scale:", pixel_group), self._percent_spin)
        layout.addWidget(pixel_group)

        physical_group = QGroupBox(self)
        self._translation_binder.register(physical_group.setTitle, self._ts("Physical Size"))
        physical_form = self._form(physical_group)
        physical_form.addRow(self._label("Width:", physical_group), self._physical_width_spin)
        physical_form.addRow(self._label("Height:", physical_group), self._physical_height_spin)
        physical_form.addRow(self._label("Unit:", physical_group), self._unit_combo)
        layout.addWidget(physical_group)

        resolution_group = QGroupBox(self)
        self._translation_binder.register(resolution_group.setTitle, self._ts("Resolution"))
        resolution_form = self._form(resolution_group)
        resolution_form.addRow(self._label("DPI:", resolution_group), self._dpi_spin)
        layout.addWidget(resolution_group)

        options_group = QGroupBox(self)
        self._translation_binder.register(options_group.setTitle, self._ts("Options"))
        options_form = self._form(options_group)
        options_form.addRow(self._constrain_check)
        options_form.addRow(self._resample_check)
        options_form.addRow(self._label("Interpolation:", options_group), self._interpolation_combo)
        options_form.addRow(self._estimate_label)
        layout.addWidget(options_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            self._translation_binder.register(ok_button.setText, self._ts("OK"))
        if cancel_button is not None:
            self._translation_binder.register(cancel_button.setText, self._ts("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _form(self, group: QGroupBox) -> QFormLayout:
        form = QFormLayout(group)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        return form

    def _label(self, source: str, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        self._translation_binder.register(label.setText, self._ts(source))
        return label

    def _connect_signals(self) -> None:
        self._width_spin.valueChanged.connect(self._on_width_changed)
        self._height_spin.valueChanged.connect(self._on_height_changed)
        self._percent_spin.valueChanged.connect(self._on_percent_changed)
        self._physical_width_spin.valueChanged.connect(self._on_physical_width_changed)
        self._physical_height_spin.valueChanged.connect(self._on_physical_height_changed)
        self._dpi_spin.valueChanged.connect(self._on_dpi_changed)
        self._unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        self._resample_check.toggled.connect(self._on_resample_toggled)

    # -- linkage ----------------------------------------------------------------

    def _on_width_changed(self, value: int) -> None:
        if self._updating:
            return
        if self._constrain_check.isChecked():
            height = self._planner.constrained_height(
                value, self._source_width, self._source_height
            )
            self._set_spin(self._height_spin, height)
        self._refresh_derived()

    def _on_height_changed(self, value: int) -> None:
        if self._updating:
            return
        if self._constrain_check.isChecked():
            width = self._planner.constrained_width(value, self._source_width, self._source_height)
            self._set_spin(self._width_spin, width)
        self._refresh_derived()

    def _on_percent_changed(self, value: float) -> None:
        if self._updating:
            return
        self._set_spin(
            self._width_spin, self._planner.pixels_for_percent(self._source_width, value)
        )
        self._set_spin(
            self._height_spin, self._planner.pixels_for_percent(self._source_height, value)
        )
        self._refresh_derived()

    def _on_physical_width_changed(self, value: float) -> None:
        if self._updating:
            return
        inches = value / self._unit_factor()
        if self._resample_check.isChecked():
            width = self._planner.pixels_for_inches(inches, self._dpi_spin.value())
            self._set_spin(self._width_spin, width)
            if self._constrain_check.isChecked():
                self._set_spin(
                    self._height_spin,
                    self._planner.constrained_height(
                        width, self._source_width, self._source_height
                    ),
                )
        else:
            self._set_dpi_from_physical(self._width_spin.value(), inches)
        self._refresh_derived()

    def _on_physical_height_changed(self, value: float) -> None:
        if self._updating:
            return
        inches = value / self._unit_factor()
        if self._resample_check.isChecked():
            height = self._planner.pixels_for_inches(inches, self._dpi_spin.value())
            self._set_spin(self._height_spin, height)
            if self._constrain_check.isChecked():
                self._set_spin(
                    self._width_spin,
                    self._planner.constrained_width(
                        height, self._source_width, self._source_height
                    ),
                )
        else:
            self._set_dpi_from_physical(self._height_spin.value(), inches)
        self._refresh_derived()

    def _set_dpi_from_physical(self, pixels: int, inches: float) -> None:
        try:
            dpi = self._planner.dpi_for_inches(pixels, inches)
        except ValueError:
            return
        clamped = max(1, min(_MAX_DPI, self._planner.round_half_up(dpi)))
        self._set_spin(self._dpi_spin, clamped)
        self._last_dpi = clamped

    def _on_dpi_changed(self, value: int) -> None:
        if self._updating:
            return
        if self._resample_check.isChecked():
            # Keep the physical size: pixels follow the new density.
            width_in = self._planner.inches(self._width_spin.value(), self._last_dpi)
            height_in = self._planner.inches(self._height_spin.value(), self._last_dpi)
            width = self._planner.pixels_for_inches(width_in, value)
            if self._constrain_check.isChecked():
                height = self._planner.constrained_height(
                    width, self._source_width, self._source_height
                )
            else:
                height = self._planner.pixels_for_inches(height_in, value)
            self._set_spin(self._width_spin, width)
            self._set_spin(self._height_spin, height)
        self._last_dpi = int(value)
        self._refresh_derived()

    def _on_unit_changed(self, _index: int) -> None:
        self._refresh_derived()

    def _on_resample_toggled(self, checked: bool) -> None:
        for widget in (
            self._width_spin,
            self._height_spin,
            self._percent_spin,
            self._interpolation_combo,
        ):
            widget.setEnabled(bool(checked))
        if not checked:
            # Pixels are locked to the document when not resampling.
            self._set_spin(self._width_spin, self._source_width)
            self._set_spin(self._height_spin, self._source_height)
        self._refresh_derived()

    def _refresh_derived(self) -> None:
        width = int(self._width_spin.value())
        height = int(self._height_spin.value())
        dpi = int(self._dpi_spin.value())
        factor = self._unit_factor()
        self._set_spin(
            self._percent_spin, self._planner.percent_for_pixels(width, self._source_width)
        )
        self._set_spin(self._physical_width_spin, self._planner.inches(width, dpi) * factor)
        self._set_spin(self._physical_height_spin, self._planner.inches(height, dpi) * factor)
        unit_suffix = " " + self._translate_runtime(self._unit_label())
        self._physical_width_spin.setSuffix(unit_suffix)
        self._physical_height_spin.setSuffix(unit_suffix)
        self._source_label.setText(
            self._translate_runtime("Current: {width} x {height} px at {dpi} dpi").format(
                width=self._source_width, height=self._source_height, dpi=self._source_dpi
            )
        )
        estimate = self._planner.estimate_history_bytes(
            width,
            height,
            self._layer_count,
            source_size=(self._source_width, self._source_height),
        )
        self._estimate_label.setText(
            self._translate_runtime("Estimated undo memory: {megabytes} MB").format(
                megabytes=f"{estimate / (1024 * 1024):.1f}"
            )
        )

    def _set_spin(self, spin: QSpinBox | QDoubleSpinBox, value: float) -> None:
        was_updating = self._updating
        self._updating = True
        try:
            spin.setValue(value)
        finally:
            self._updating = was_updating

    def _unit_factor(self) -> float:
        unit_id = self._unit_combo.currentData()
        for candidate, factor, _label in _UNITS:
            if candidate == unit_id:
                return factor
        return _UNITS[0][1]

    def _unit_label(self) -> str:
        unit_id = self._unit_combo.currentData()
        for candidate, _factor, label in _UNITS:
            if candidate == unit_id:
                return label
        return _UNITS[0][2]

    # -- i18n -------------------------------------------------------------------

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        for index in range(self._interpolation_combo.count()):
            mode = InterpolationMode.normalize(self._interpolation_combo.itemData(index))
            if mode is not None:
                self._interpolation_combo.setItemText(
                    index, self._translate_runtime(_INTERPOLATION_LABELS[mode])
                )
        for index in range(self._unit_combo.count()):
            unit_id = self._unit_combo.itemData(index)
            for candidate, _factor, label in _UNITS:
                if candidate == unit_id:
                    self._unit_combo.setItemText(index, self._translate_runtime(label))
        self._refresh_derived()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ImageSizeDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
