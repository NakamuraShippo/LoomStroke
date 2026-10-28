"""Session-only print mark controls with stable option values."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QWidget,
)

from ...core.print_mark_models import PrintMarkOptions
from ..translations import translate_with_fallback

STYLES = (
    ("jp_corner", "Japanese corner (current)"),
    ("jp_double", "Japanese double"),
    ("western", "Western crop marks"),
)
COLORS = (
    ("legacy", "Current black (RGB)"),
    ("k_only", "K only (100%)"),
    ("registration", "Registration (CMYK 100%)"),
)


class PrintMarkControls(QGroupBox):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        self.style, self.color = QComboBox(self), QComboBox(self)
        self.style_label, self.color_label = QLabel(self), QLabel(self)
        for value, source in STYLES:
            self.style.addItem(source, value)
        for value, source in COLORS:
            self.color.addItem(source, value)
        form.addRow(self.style_label, self.style)
        self.flags = {}
        for key in ("crop", "bleed", "registration", "center"):
            box = QCheckBox(self)
            box.setChecked(key == "crop")
            box.toggled.connect(self.changed)
            self.flags[key] = box
            form.addRow(box)
        self.width_pt, self.offset_pt = QDoubleSpinBox(self), QDoubleSpinBox(self)
        self.width_pt.setRange(0.01, 5)
        self.width_pt.setDecimals(2)
        self.width_pt.setValue(0.25)
        self.offset_pt.setRange(0, 144)
        self.offset_pt.setDecimals(2)
        self.offset_pt.setValue(3)
        self.width_label, self.offset_label = QLabel(self), QLabel(self)
        form.addRow(self.width_label, self.width_pt)
        form.addRow(self.offset_label, self.offset_pt)
        form.addRow(self.color_label, self.color)
        self.style.currentIndexChanged.connect(self.changed)
        self.color.currentIndexChanged.connect(self.changed)
        self.width_pt.valueChanged.connect(self.changed)
        self.offset_pt.valueChanged.connect(self.changed)
        self._retranslate()

    def options(self) -> PrintMarkOptions:
        return PrintMarkOptions(
            style=str(self.style.currentData()),
            color=str(self.color.currentData()),
            width_pt=self.width_pt.value(),
            offset_pt=self.offset_pt.value(),
            **{key: box.isChecked() for key, box in self.flags.items()},
        )

    @staticmethod
    def _ts(source: str) -> str:
        return translate_with_fallback("PrintMarkControls", source)

    def _retranslate(self) -> None:
        self.setTitle(self._ts("Print marks"))
        for combo, values in ((self.style, STYLES), (self.color, COLORS)):
            for index, (_, source) in enumerate(values):
                combo.setItemText(index, self._ts(source))
        for key, source in (
            ("crop", "Crop marks"),
            ("bleed", "Bleed marks"),
            ("registration", "Registration marks"),
            ("center", "Center marks"),
        ):
            self.flags[key].setText(self._ts(source))
        self.style_label.setText(self._ts("Style"))
        self.color_label.setText(self._ts("Mark colour"))
        self.width_label.setText(self._ts("Line width (pt)"))
        self.offset_label.setText(self._ts("Offset outside bleed (pt)"))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()

    def refresh_translations(self) -> None:
        self._retranslate()
