"""Range-only character geometry controls on the Font panel."""

from PySide6.QtCore import QEvent, QSignalBlocker, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QLabel, QWidget

from loomstroke.core.japanese_text.character_geometry import GEOMETRY_LIMITS

from .typography_widget import _ts


class CharacterGeometryWidget(QWidget):
    requested = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.controls = {}
        self.labels = []
        sources = (
            "Horizontal scale",
            "Vertical scale",
            "Baseline shift",
            "Space before character",
            "Space after character",
        )
        for (key, (default, lower, upper)), source in zip(
            GEOMETRY_LIMITS.items(), sources, strict=True
        ):
            spin = QDoubleSpinBox(self)
            spin.setRange(lower, upper)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
            spin.setKeyboardTracking(False)
            spin.setValue(default)
            spin.setSuffix(
                " ×" if key.endswith("scale") else " em"  # noqa: RUF001 - scale multiplier
            )
            spin.valueChanged.connect(lambda value, k=key: self.requested.emit(k, value))
            label = QLabel(self)
            self.labels.append((label, spin, source))
            self.controls[key] = spin
            form.addRow(label, spin)
        self.refresh_translations()

    def set_values(self, values):
        for key, spin in self.controls.items():
            blocker = QSignalBlocker(spin)
            spin.setValue(values.get(key, GEOMETRY_LIMITS[key][0]))
            del blocker
        self.setEnabled(bool(values.get("color_range_selected", False)))

    def refresh_translations(self):
        for label, spin, source in self.labels:
            label.setText(_ts(source))
            spin.setAccessibleName(_ts(source))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
