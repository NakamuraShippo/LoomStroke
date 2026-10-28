"""Configure new effect shapes before the existing insertion command."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
)

from loomstroke.plugins.vector_tools.effect_shapes import effect_params, effect_style
from loomstroke.plugins.vector_tools.parametric_shapes import build_parametric_shape_object
from loomstroke.ui.translations import translate_with_fallback

_STREAM_FIELDS = (
    ("direction", "Direction (degrees)", -360, 360),
    ("count", "Line count", 1, 512),
    ("length_jitter", "Length variation", 0, 1),
    ("width", "Width ratio", 0.0001, 0.2),
    ("taper", "Taper ratio", 0, 0.5),
    ("seed", "Random seed", 0, 2147483647),
)
_FLASH_FIELDS = (
    ("center_x", "Centre X offset ratio", -1, 1),
    ("center_y", "Centre Y offset ratio", -1, 1),
    ("inner", "Inner diameter ratio", 0, 1),
    ("outer", "Outer diameter ratio", 0.001, 1),
    ("count", "Line count", 3, 256),
    ("variation", "Variation", 0, 1),
    ("seed", "Random seed", 0, 2147483647),
)


class EffectShapeDialog(QDialog):
    def __init__(self, shape_id, bbox, selection_bounds=None, parent=None):
        super().__init__(parent)
        self.shape_id, self.default_bbox, self.selection_bounds = shape_id, bbox, selection_bounds
        form = QFormLayout(self)
        self.labels = []
        self.range = QComboBox(self)
        self.range.addItem("", "rectangle")
        if selection_bounds is not None:
            self.range.addItem("", "selection_bounds")
        self._row(form, "Range", self.range)
        self.bounds = []
        for source, value in zip(("Left", "Top", "Right", "Bottom"), bbox, strict=True):
            spin = QDoubleSpinBox(self)
            spin.setRange(-1000000, 1000000)
            spin.setDecimals(4)
            spin.setValue(value)
            self.bounds.append(spin)
            self._row(form, source, spin)
        defaults = effect_params(shape_id, {})
        self.fields = {}
        fields = _STREAM_FIELDS if shape_id == "stream_lines" else _FLASH_FIELDS
        for key, source, low, high in fields:
            spin = QSpinBox(self) if key in ("count", "seed") else QDoubleSpinBox(self)
            spin.setRange(low, high)
            if isinstance(spin, QDoubleSpinBox):
                spin.setDecimals(4)
            spin.setValue(defaults[key])
            self.fields[key] = spin
            self._row(form, source, spin)
        self.white = QCheckBox(self)
        if shape_id != "stream_lines":
            form.addRow(self.white)
        self.error = QLabel(self)
        form.addRow(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._retranslate()

    def _row(self, form, source, widget):
        label = QLabel(self)
        self.labels.append((label, source))
        form.addRow(label, widget)

    def object_value(self):
        bbox = tuple(spin.value() for spin in self.bounds)
        if self.range.currentData() == "selection_bounds":
            x, y, w, h = self.selection_bounds
            bbox = (x, y, x + w, y + h)
        params = effect_params(self.shape_id, {k: s.value() for k, s in self.fields.items()})
        if self.shape_id != "stream_lines":
            params["white"] = int(self.white.isChecked())
        return build_parametric_shape_object(
            self.shape_id, bbox, params, style=effect_style(params)
        )

    def accept(self):
        try:
            if self.object_value() is None:
                raise ValueError("No effect geometry")
        except ValueError:
            self.error.setText(self._ts("Invalid effect bounds."))
            return
        super().accept()

    @staticmethod
    def _ts(source):
        return translate_with_fallback("EffectShapeDialog", source)

    def _retranslate(self):
        self.setWindowTitle(self._ts("Effect Lines"))
        self.white.setText(self._ts("White fill"))
        for label, source in self.labels:
            label.setText(self._ts(source))
        self.range.setItemText(0, self._ts("Rectangle"))
        if self.range.count() > 1:
            self.range.setItemText(1, self._ts("Selection bounding rectangle"))
        if self.error.text():
            self.error.setText(self._ts("Invalid effect bounds."))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def configure_effect_shape(host, shape_id, pos, size, parent):
    pages = getattr(host, "pages", None)
    before = pages.get_active_page_id() if pages else None
    getter = getattr(host.canvas, "get_selection_bounds", None)
    selection = getter() if callable(getter) else None
    if isinstance(selection, QRect):
        selection = (selection.x(), selection.y(), selection.width(), selection.height())
    bbox = (pos[0] - size / 2, pos[1] - size / 2, pos[0] + size / 2, pos[1] + size / 2)
    dialog = EffectShapeDialog(shape_id, bbox, selection, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    if pages and pages.get_active_page_id() != before:
        return None
    return dialog.object_value()
