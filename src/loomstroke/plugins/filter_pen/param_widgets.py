"""Filter-schema widgets with no layer selection or image-editing dependencies."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QWidget,
)

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.core.filters import FilterParamSchema, FilterRegistry
from loomstroke.plugins.filter_panel.filter_graph_widgets import (
    CurvesGraphWidget,
    LevelsGraphWidget,
)
from loomstroke.plugins.filter_panel.gradient_stops_widget import GradientStopsWidget

from .translations import filter_text, ts


class NumericParameterRow(QWidget):
    """Bounded slider and precise spinbox for one independent filter setting."""

    valueChanged = Signal(object)

    def __init__(self, schema: FilterParamSchema, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._integer = schema.param_type == "int"
        self._lo = float(schema.min_val if schema.min_val is not None else -1_000_000)
        self._hi = float(schema.max_val if schema.max_val is not None else 1_000_000)
        self._step = float(schema.step or (1 if self._integer else 0.01))
        self._positions = min(2000, max(1, round((self._hi - self._lo) / self._step)))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, self._positions)
        self.slider.setVisible(schema.min_val is not None and schema.max_val is not None)
        if self._integer:
            self.spin: QSpinBox | QDoubleSpinBox = QSpinBox(self)
            self.spin.setRange(int(self._lo), int(self._hi))
            self.spin.setSingleStep(max(1, int(self._step)))
        else:
            self.spin = QDoubleSpinBox(self)
            self.spin.setRange(self._lo, self._hi)
            self.spin.setDecimals(max(2, min(6, math.ceil(-math.log10(self._step)))))
            self.spin.setSingleStep(self._step)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)
        self.setValue(schema.default)
        self.slider.valueChanged.connect(self._slider_changed)
        self.spin.valueChanged.connect(self._spin_changed)

    def value(self) -> int | float:
        value = self.spin.value()
        return int(value) if self._integer else float(value)

    def setValue(self, value: Any) -> None:  # noqa: N802 - Qt widget API
        number = float(value)
        if not math.isfinite(number):
            return
        number = max(self._lo, min(self._hi, number))
        previous = self.spin.blockSignals(True)
        self.spin.setValue(int(number) if self._integer else number)
        self.spin.blockSignals(previous)
        self._sync_slider()

    def _sync_slider(self) -> None:
        fraction = (self.value() - self._lo) / max(1e-12, self._hi - self._lo)
        previous = self.slider.blockSignals(True)
        self.slider.setValue(round(fraction * self._positions))
        self.slider.blockSignals(previous)

    def _slider_changed(self, position: int) -> None:
        value = self._lo + position / self._positions * (self._hi - self._lo)
        value = self._lo + round((value - self._lo) / self._step) * self._step
        self.setValue(value)
        self.valueChanged.emit(self.value())

    def _spin_changed(self, _value: float) -> None:
        self._sync_slider()
        self.valueChanged.emit(self.value())


class FilterParameterForm(QWidget):
    """Build every registered filter parameter and emit settings-only changes."""

    changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filter_type = ""
        self._updating = False
        self.widgets: dict[str, QWidget] = {}
        self._levels_graph: LevelsGraphWidget | None = None
        self._labels: dict[str, QLabel] = {}
        self._schemas: list[FilterParamSchema] = []
        self._form = QFormLayout(self)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    @property
    def filter_type(self) -> str:
        return self._filter_type

    def set_filter(
        self,
        filter_type: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        effect_type: str | None = None,
    ) -> None:
        if effect_type is not None:
            if filter_type is not None and filter_type != effect_type:
                raise TypeError("Conflicting filter_type and effect_type")
            filter_type = effect_type
        if filter_type is None or params is None:
            raise TypeError("set_filter() requires filter_type and params")
        self._updating = True
        try:
            while self._form.rowCount():
                self._form.removeRow(0)
            self.widgets.clear()
            self._labels.clear()
            self._levels_graph = None
            self._filter_type = filter_type
            filter_cls = FilterRegistry.get(filter_type)
            if filter_cls is None:
                raise ValueError(f"Unknown filter: {filter_type}")
            self._schemas = filter_cls().param_schema()
            if filter_type == "levels":
                self._levels_graph = LevelsGraphWidget(self)
                self._levels_graph.value_changed.connect(self._levels_changed)
                self._form.addRow(self._levels_graph)
            for schema in self._schemas:
                widget = self._make_widget(schema)
                label = QLabel(filter_text(schema.label), self)
                self._labels[schema.key] = label
                self.widgets[schema.key] = widget
                self._form.addRow(label, widget)
            if not self._schemas:
                self._form.addRow(QLabel(ts("No adjustable parameters"), self))
            self.set_values(params)
        finally:
            self._updating = False

    effect_type = filter_type
    set_effect = set_filter

    def _make_widget(self, schema: FilterParamSchema) -> QWidget:  # noqa: PLR0911
        if schema.param_type in {"float", "int"}:
            numeric = NumericParameterRow(schema, self)
            numeric.valueChanged.connect(self._emit_changed)
            return numeric
        if schema.param_type == "bool":
            checkbox = QCheckBox(self)
            checkbox.setChecked(bool(schema.default))
            checkbox.toggled.connect(self._emit_changed)
            return checkbox
        if schema.param_type == "enum":
            combo = QComboBox(self)
            for choice in schema.choices or []:
                combo.addItem(filter_text(str(choice).replace("_", " ").title()), choice)
            combo.currentIndexChanged.connect(self._emit_changed)
            return combo
        if schema.param_type == "curve_points":
            curve = CurvesGraphWidget(self)
            curve.valueChanged.connect(self._emit_changed)
            return curve
        if schema.param_type == "gradient_stops":
            gradient = GradientStopsWidget(self)
            gradient.stops_changed.connect(self._emit_changed)
            gradient._btn_delete.setToolTip(ts("Delete selected stop"))
            return gradient
        raise ValueError(f"Unsupported filter parameter: {schema.param_type}")

    def values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, widget in self.widgets.items():
            if isinstance(widget, QComboBox):
                values[key] = widget.currentData()
            elif isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
            else:
                values[key] = deepcopy(widget.value())
        return values

    def set_values(self, params: dict[str, Any]) -> None:
        previous_updating = self._updating
        self._updating = True
        try:
            for schema in self._schemas:
                widget = self.widgets[schema.key]
                value = deepcopy(params.get(schema.key, schema.default))
                previous = widget.blockSignals(True)
                try:
                    if isinstance(widget, QComboBox):
                        widget.setCurrentIndex(max(0, widget.findData(value)))
                    elif isinstance(widget, QCheckBox):
                        widget.setChecked(bool(value))
                    else:
                        widget.setValue(value)
                finally:
                    widget.blockSignals(previous)
            if self._levels_graph is not None:
                self._levels_graph.set_params(self.values())
        finally:
            self._updating = previous_updating

    def _emit_changed(self, *_args: Any) -> None:
        if self._updating:
            return
        params = self.values()
        if self._levels_graph is not None:
            self._levels_graph.set_params(params)
        self.changed.emit(params)

    def _levels_changed(self, key: str, value: Any) -> None:
        widget = self.widgets.get(key)
        if isinstance(widget, NumericParameterRow):
            widget.setValue(value)
            self._emit_changed()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() != QEvent.Type.LanguageChange:
            return
        for schema in self._schemas:
            self._labels[schema.key].setText(filter_text(schema.label))
            widget = self.widgets[schema.key]
            if isinstance(widget, QComboBox):
                for index in range(widget.count()):
                    source = str(widget.itemData(index)).replace("_", " ").title()
                    widget.setItemText(index, filter_text(source))
            elif isinstance(widget, GradientStopsWidget):
                widget._btn_delete.setToolTip(ts("Delete selected stop"))


# Compatibility aliases for existing imports.
EffectParameterForm = FilterParameterForm


_bind_legacy_names(
    __name__,
    {
        "EffectParamSchema": "FilterParamSchema",
        "EffectRegistry": "FilterRegistry",
        "effect_text": "filter_text",
        "EffectParameterForm": "FilterParameterForm",
    },
)
