"""Select and edit bounded relief light sources without rebuilding the style form."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from loomstroke.core.appearance import AppearanceKind, AppearanceParam
from loomstroke.core.appearance.relief_style_parameters import MAX_LIGHTS, relief_lights
from loomstroke.ui.translations import translated_literal_domain


def _tr(source: str) -> str:
    return translated_literal_domain("AppearancePanel", source, use_fallback=True)


class LayerStyleLightsEditor(QWidget):
    def __init__(self, params: dict[str, Any], on_change: Any, color_picker: Any = None) -> None:
        super().__init__()
        # Delay the recursive form import until construction to avoid an import cycle.
        from .appearance_param_form import AppearanceParamForm  # noqa: PLC0415

        self.setObjectName("relief_lights")
        self._implicit = not params.get("lights")
        self._lights = relief_lights(params)
        self._on_change = on_change
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.setObjectName("light_selector")
        self.selector.setAccessibleName(_tr("Light sources"))
        self.add_button = QPushButton(_tr("Add light"))
        self.add_button.setObjectName("add_light")
        self.remove_button = QPushButton(_tr("Remove light"))
        self.remove_button.setObjectName("remove_light")
        row.addWidget(self.selector, 1)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        layout.addLayout(row)
        self._schema = AppearanceKind(
            "light",
            "style",
            "Light",
            (
                AppearanceParam("enabled", "bool", True, "Enabled"),
                AppearanceParam("angle", "float", 135.0, "Light direction", 0, 360, step=1),
                AppearanceParam("elevation", "float", 45.0, "Elevation", 0, 90, step=1),
                AppearanceParam("intensity", "float", 1.0, "Intensity", 0, 2, step=0.05),
                AppearanceParam("color", "color", [255] * 4, "Color"),
            ),
        )
        self.form = AppearanceParamForm(
            numeric_sliders=True, on_change=self._edited, color_picker=color_picker
        )
        layout.addWidget(self.form)
        self._refresh(0)
        self.selector.currentIndexChanged.connect(self._select)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)

    def value(self) -> list[dict[str, Any]]:
        return [] if self._implicit else deepcopy(self._lights)

    def _refresh(self, index: int) -> None:
        self.selector.blockSignals(True)
        self.selector.clear()
        for number, light in enumerate(self._lights, 1):
            self.selector.addItem(_tr("Light {number}").format(number=number), light["id"])
        self.selector.setCurrentIndex(index)
        self.selector.blockSignals(False)
        self.add_button.setEnabled(len(self._lights) < MAX_LIGHTS)
        self.remove_button.setEnabled(len(self._lights) > 1)
        self._select(index)

    def _select(self, index: int) -> None:
        if 0 <= index < len(self._lights):
            self.form.set_kind(self._schema, self._lights[index])

    def _edited(self) -> None:
        index = self.selector.currentIndex()
        if index < 0:
            return
        self._lights[index].update(self.form.values())
        self._implicit = False
        self._on_change()

    def _add(self) -> None:
        if len(self._lights) >= MAX_LIGHTS:
            return
        source = deepcopy(self._lights[self.selector.currentIndex()])
        source.update(id=uuid4().hex, angle=(source["angle"] + 90) % 360, enabled=True)
        self._lights.append(source)
        self._implicit = False
        self._refresh(len(self._lights) - 1)
        self._on_change()

    def _remove(self) -> None:
        if len(self._lights) <= 1:
            return
        index = self.selector.currentIndex()
        self._lights.pop(index)
        self._implicit = False
        self._refresh(min(index, len(self._lights) - 1))
        self._on_change()
