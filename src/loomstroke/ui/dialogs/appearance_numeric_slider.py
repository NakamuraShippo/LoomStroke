"""Synchronized slider and exact numeric input for an appearance parameter."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSlider, QSpinBox, QWidget

from loomstroke.core.appearance import AppearanceParam


class AppearanceNumericSlider(QWidget):
    def __init__(self, param: AppearanceParam, value: float, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._integer = param.kind == "int"
        self._scale = 1 if self._integer else 1000
        minimum = param.minimum if param.minimum is not None else -100000
        maximum = param.maximum if param.maximum is not None else 100000
        self.spin: QSpinBox | QDoubleSpinBox
        if self._integer:
            self.spin = QSpinBox()
            self.spin.setRange(int(minimum), int(maximum))
            self.spin.setSingleStep(int(param.step or 1))
            self.spin.setValue(int(value))
        else:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(3)
            self.spin.setRange(float(minimum), float(maximum))
            self.spin.setSingleStep(float(param.step or 0.1))
            self.spin.setValue(float(value))
        self.spin.setObjectName(f"{param.key}_value")
        self.spin.setMinimumWidth(85)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName(f"{param.key}_slider")
        self.slider.setRange(round(minimum * self._scale), round(maximum * self._scale))
        self.slider.setSingleStep(max(1, round((param.step or 1) * self._scale)))
        self.slider.setPageStep(self.slider.singleStep() * 5)
        self.slider.setValue(round(self.spin.value() * self._scale))
        self.slider.setTracking(True)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self._on_change = on_change
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)

    def value(self) -> int | float:
        return self.spin.value()

    def _from_slider(self, value: int) -> None:
        self.spin.blockSignals(True)
        if isinstance(self.spin, QSpinBox):
            self.spin.setValue(value)
        else:
            self.spin.setValue(value / self._scale)
        self.spin.blockSignals(False)
        self._on_change()

    def _from_spin(self, value: int | float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(value * self._scale))
        self.slider.blockSignals(False)
        self._on_change()
