"""Performance-page slider for the ``[`` / ``]`` brush size key acceleration."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from loomstroke.core.brush_size_key_stepper import (
    ACCELERATION_SETTING_KEY,
    normalize_acceleration_level,
)

# Slider positions map to the stable stored level values.
LEVEL_VALUES = ("none", "weak", "standard", "strong")


class BrushSizeAccelerationControl:
    LABEL = "Brush Size Key Acceleration"
    TOOLTIP = (
        "How much holding or repeatedly pressing [ / ] speeds up brush size changes. "
        "Off changes the size by the same ratio on every press."
    )
    LEVEL_LABELS = ("Off", "Weak", "Standard", "Strong")

    def __init__(self, page: Any) -> None:
        self.page = page
        self.widget = QWidget(page)
        layout = QHBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal, self.widget)
        self.slider.setRange(0, len(LEVEL_VALUES) - 1)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTickInterval(1)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.value_label = QLabel(self.widget)
        self.value_label.setMinimumWidth(48)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)
        page._translation_binder.register(self.slider.setToolTip, page._ts(self.TOOLTIP))
        self.slider.valueChanged.connect(self.refresh_value_label)

    def add_to(self, form: Any) -> None:
        self.page._add_labeled_row(form, self.page._ts(self.LABEL), self.widget)

    def load(self) -> None:
        level = normalize_acceleration_level(self.page.settings.get(ACCELERATION_SETTING_KEY))
        self.slider.setValue(LEVEL_VALUES.index(level))
        self.refresh_value_label()

    def refresh_value_label(self, _value: int | None = None) -> None:
        text = self.LEVEL_LABELS[self.slider.value()]
        self.value_label.setText(self.page._translate_runtime(text))

    def collect_values(self) -> dict[str, Any]:
        return {ACCELERATION_SETTING_KEY: LEVEL_VALUES[self.slider.value()]}
