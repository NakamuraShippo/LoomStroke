"""Curve controls with direct point editing and synchronized numeric sliders."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from loomstroke.core.appearance import AppearanceParam
from loomstroke.core.appearance.relief_style_parameters import IDENTITY_PROFILE, clean_profile
from loomstroke.ui.translations import translated_literal_domain

from .appearance_numeric_slider import AppearanceNumericSlider
from .layer_style_profile_canvas import LayerStyleProfileCanvas


def _tr(source: str) -> str:
    return translated_literal_domain("AppearancePanel", source, use_fallback=True)


class LayerStyleProfileEditor(QWidget):
    def __init__(self, value: dict[str, Any], on_change: Any) -> None:
        super().__init__()
        self.setObjectName("relief_profile")
        self.profile = clean_profile(value)
        self.selected = 0
        self._on_change = on_change
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = LayerStyleProfileCanvas(self)
        self.canvas.setAccessibleName(_tr("Profile"))
        self.canvas.setToolTip(
            _tr(
                "Click to add a point. Drag to adjust. "
                "Right-click or Delete removes an interior point."
            )
        )
        layout.addWidget(self.canvas)
        row = QHBoxLayout()
        self.interpolation = QComboBox()
        self.interpolation.setObjectName("profile_interpolation")
        self.interpolation.setAccessibleName(_tr("Interpolation"))
        for source, key in (("Linear", "linear"), ("Smooth", "smooth")):
            self.interpolation.addItem(_tr(source), key)
        self.interpolation.setCurrentIndex(int(self.profile["interpolation"] == "smooth"))
        self.interpolation.currentIndexChanged.connect(self._interpolation_changed)
        reset = QPushButton(_tr("Reset profile"))
        reset.setObjectName("reset_profile")
        reset.clicked.connect(self._reset)
        self.remove_button = QPushButton(_tr("Remove point"))
        self.remove_button.setObjectName("remove_profile_point")
        self.remove_button.clicked.connect(self.remove_point)
        row.addWidget(self.interpolation)
        row.addWidget(reset)
        row.addWidget(self.remove_button)
        layout.addLayout(row)
        form = QFormLayout()
        self.x_control = AppearanceNumericSlider(
            AppearanceParam("profile_x", "float", 0.0, "Input", 0, 1, step=0.01),
            0.0,
            self._slider_changed,
        )
        self.y_control = AppearanceNumericSlider(
            AppearanceParam("profile_y", "float", 0.0, "Output", 0, 1, step=0.01),
            0.0,
            self._slider_changed,
        )
        for label, widget in (("Input", self.x_control), ("Output", self.y_control)):
            widget.slider.setAccessibleName(_tr(label))
            widget.spin.setAccessibleName(_tr(label))
            form.addRow(_tr(label), widget)
        layout.addLayout(form)
        self.sync_controls()

    def value(self) -> dict[str, Any]:
        return deepcopy(self.profile)

    def sync_controls(self) -> None:
        points = self.profile["points"]
        for widget, value in (
            (self.x_control, points[self.selected][0]),
            (self.y_control, points[self.selected][1]),
        ):
            widget.spin.blockSignals(True)
            widget.slider.blockSignals(True)
            widget.spin.setValue(value)
            widget.slider.setValue(round(value * 1000))
            widget.spin.blockSignals(False)
            widget.slider.blockSignals(False)
        interior = 0 < self.selected < len(points) - 1
        self.x_control.setEnabled(interior)
        self.remove_button.setEnabled(interior)

    def move_point(self, x: float, y: float) -> None:
        points = self.profile["points"]
        if 0 < self.selected < len(points) - 1:
            x = max(
                points[self.selected - 1][0] + 0.001, min(points[self.selected + 1][0] - 0.001, x)
            )
        else:
            x = points[self.selected][0]
        points[self.selected] = [x, max(0.0, min(1.0, y))]
        self.changed()

    def _slider_changed(self) -> None:
        self.move_point(float(self.x_control.value()), float(self.y_control.value()))

    def remove_point(self) -> None:
        if 0 < self.selected < len(self.profile["points"]) - 1:
            self.profile["points"].pop(self.selected)
            self.selected = min(self.selected, len(self.profile["points"]) - 1)
            self.changed()

    def _interpolation_changed(self) -> None:
        self.profile["interpolation"] = self.interpolation.currentData()
        self.changed()

    def _reset(self) -> None:
        self.profile = deepcopy(IDENTITY_PROFILE)
        self.selected = 0
        self.interpolation.blockSignals(True)
        self.interpolation.setCurrentIndex(0)
        self.interpolation.blockSignals(False)
        self.changed()

    def changed(self) -> None:
        self.sync_controls()
        self.canvas.update()
        self._on_change()
