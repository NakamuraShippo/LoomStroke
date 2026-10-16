"""Shared factory for creating property widgets from PropertySpec.

Extracted from ToolPropertiesPanel so that both the fixed property bar
and the floating ContextToolbar can reuse the same widget creation logic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from loomstroke.plugins.sdk.tool import ContextWidgetSpec

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QWidget,
)

from loomstroke.plugins.sdk.tool import PropertySpec, PropertyType
from loomstroke.ui.widgets.spinbox_sizing import fit_spinbox_to_values


def create_widget_for_spec(
    spec: PropertySpec,
    *,
    parent: QWidget | None = None,
    on_changed: Callable[[str, Any], None],
    compact: bool = False,
    translate_fn: Callable[[str], str] | None = None,
) -> tuple[QWidget, Callable[[Any], None], Callable[[], Any]]:
    """Create a UI widget from a PropertySpec.

    Returns:
        (widget, setter, getter) tuple.
    """
    if spec.type in {PropertyType.FLOAT, PropertyType.INT}:
        return _create_numeric_widget(spec, parent=parent, on_changed=on_changed, compact=compact)
    if spec.type == PropertyType.BOOL:
        return _create_bool_widget(spec, parent=parent, on_changed=on_changed)
    if spec.type == PropertyType.ENUM:
        return _create_enum_widget(
            spec, parent=parent, on_changed=on_changed, translate_fn=translate_fn
        )
    if spec.type == PropertyType.COLOR:
        return _create_color_widget(spec, parent=parent, on_changed=on_changed, compact=compact)
    if spec.type == PropertyType.STRING:
        return _create_string_widget(spec, parent=parent, on_changed=on_changed)
    # Fallback
    spin = QDoubleSpinBox(parent)
    spin.valueChanged.connect(lambda value: on_changed(spec.key, value))
    return spin, spin.setValue, spin.value


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _create_string_widget(
    spec: PropertySpec,
    *,
    parent: QWidget | None = None,
    on_changed: Callable[[str, Any], None],
) -> tuple[QWidget, Callable[[Any], None], Callable[[], Any]]:
    line_edit = QLineEdit(parent)
    line_edit.textChanged.connect(lambda text: on_changed(spec.key, text))

    def setter(value: Any) -> None:
        line_edit.blockSignals(True)
        line_edit.setText("" if value is None else str(value))
        line_edit.blockSignals(False)

    return line_edit, setter, line_edit.text


def _create_bool_widget(
    spec: PropertySpec,
    *,
    parent: QWidget | None = None,
    on_changed: Callable[[str, Any], None],
) -> tuple[QWidget, Callable[[Any], None], Callable[[], Any]]:
    checkbox = QCheckBox(parent)
    checkbox.toggled.connect(lambda checked: on_changed(spec.key, bool(checked)))

    def setter(value: Any) -> None:
        checkbox.blockSignals(True)
        checkbox.setChecked(bool(value))
        checkbox.blockSignals(False)

    return checkbox, setter, checkbox.isChecked


def _create_enum_widget(
    spec: PropertySpec,
    *,
    parent: QWidget | None = None,
    on_changed: Callable[[str, Any], None],
    translate_fn: Callable[[str], str] | None = None,
) -> tuple[QWidget, Callable[[Any], None], Callable[[], Any]]:
    combo = QComboBox(parent)
    tr = translate_fn or (lambda s: s)
    raw_items = spec.enum_items or []
    for item in raw_items:
        combo.addItem(tr(item), item)

    def read_value() -> Any:
        data = combo.currentData()
        return data if data is not None else combo.currentText()

    def set_value(value: Any) -> None:
        if value is None:
            return
        index = combo.findData(value)
        if index < 0 and isinstance(value, str):
            index = combo.findText(value)
            if index < 0:
                index = combo.findText(tr(value))
        index = max(index, 0)
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    combo.currentIndexChanged.connect(lambda _index: on_changed(spec.key, read_value()))
    return combo, set_value, read_value


def _create_numeric_widget(  # noqa: PLR0915 - compact factory assembly
    spec: PropertySpec,
    *,
    parent: QWidget | None = None,
    on_changed: Callable[[str, Any], None],
    compact: bool = False,
) -> tuple[QWidget, Callable[[Any], None], Callable[[], Any]]:
    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3 if compact else 4)

    slider = QSlider(Qt.Orientation.Horizontal, container)
    slider_multiplier = 100 if spec.type == PropertyType.FLOAT else 1
    min_value = spec.min_value if spec.min_value is not None else 0.0
    max_value = spec.max_value if spec.max_value is not None else 100.0
    slider.setRange(
        int(min_value * slider_multiplier),
        int(max_value * slider_multiplier),
    )
    slider.setSingleStep(max(1, int((spec.step or 1.0) * slider_multiplier)))

    if spec.type == PropertyType.FLOAT:
        spin_float = QDoubleSpinBox(container)
        spin_float.setDecimals(2)
        spin_float.setRange(min_value, max_value)
        if spec.step:
            spin_float.setSingleStep(spec.step)
        fit_spinbox_to_values(spin_float, _numeric_width_seed_values(spec, min_value))
        spin_widget: QDoubleSpinBox | QSpinBox = spin_float

        def set_spin_value(value: float) -> None:
            spin_float.setValue(value)

        def get_spin_value() -> float:
            return spin_float.value()

        spin_float.valueChanged.connect(lambda value: on_changed(spec.key, float(value)))
    else:
        spin_int = QSpinBox(container)
        spin_int.setRange(int(min_value), int(max_value))
        if spec.step:
            spin_int.setSingleStep(max(1, int(spec.step)))
        fit_spinbox_to_values(spin_int, _numeric_width_seed_values(spec, min_value))
        spin_widget = spin_int

        def set_spin_value(value: float) -> None:
            spin_int.setValue(int(value))

        def get_spin_value() -> float:
            return float(spin_int.value())

        spin_int.valueChanged.connect(lambda value: on_changed(spec.key, float(value)))

    def sync_from_slider(raw_value: int) -> None:
        value = raw_value / slider_multiplier
        spin_widget.blockSignals(True)
        set_spin_value(value)
        spin_widget.blockSignals(False)
        on_changed(spec.key, value)

    slider.valueChanged.connect(sync_from_slider)

    layout.addWidget(slider, 1)
    layout.addWidget(spin_widget)

    def setter(value: Any) -> None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        slider.blockSignals(True)
        spin_widget.blockSignals(True)
        slider.setValue(int(numeric * slider_multiplier))
        set_spin_value(numeric)
        slider.blockSignals(False)
        spin_widget.blockSignals(False)
        fit_spinbox_to_values(spin_widget, (numeric,))

    return container, setter, get_spin_value


def _numeric_width_seed_values(spec: PropertySpec, fallback: float) -> tuple[float, ...]:
    if spec.default_value is None:
        return (fallback,)
    try:
        return (float(spec.default_value),)
    except (TypeError, ValueError):
        return (fallback,)


def _create_color_widget(
    spec: PropertySpec,
    *,
    parent: QWidget | None = None,
    on_changed: Callable[[str, Any], None],
    compact: bool = False,
) -> tuple[QWidget, Callable[[Any], None], Callable[[], Any]]:
    button = ColorButton(parent)
    if compact:
        button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(20)
        button.setMaximumHeight(24)
    button.color_changed.connect(lambda color: on_changed(spec.key, color))
    return button, button.set_color, button.color


# ---------------------------------------------------------------------------
# ColorButton (shared between ToolPropertiesPanel and ContextToolbar)
# ---------------------------------------------------------------------------

from PySide6.QtCore import Signal  # noqa: E402


class ColorButton(QPushButton):
    """Color swatch button that opens a QColorDialog on click."""

    color_changed = Signal(tuple)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(0, 0, 0)
        self.clicked.connect(self._choose_color)
        self._update_style()

    def color(self) -> tuple[float, float, float, float]:
        return (
            self._color.redF(),
            self._color.greenF(),
            self._color.blueF(),
            self._color.alphaF(),
        )

    def set_color(self, color_value: Any) -> None:
        if isinstance(color_value, tuple) and len(color_value) in {3, 4}:
            r_raw, g_raw, b_raw = (float(c) for c in color_value[:3])
            a_raw = float(color_value[3]) if len(color_value) == 4 else 1.0
            if max(r_raw, g_raw, b_raw, a_raw) > 1.0:
                r_raw /= 255.0
                g_raw /= 255.0
                b_raw /= 255.0
                if a_raw > 1.0:
                    a_raw /= 255.0
            r = max(0.0, min(1.0, r_raw))
            g = max(0.0, min(1.0, g_raw))
            b = max(0.0, min(1.0, b_raw))
            a = max(0.0, min(1.0, a_raw))
            self._color = QColor.fromRgbF(r, g, b, a)
        elif isinstance(color_value, QColor):
            self._color = QColor(color_value)
        self._update_style()

    def _choose_color(self) -> None:
        from loomstroke.ui.widgets.color_picker_popup import ColorPickerPopup

        color = ColorPickerPopup.pick_color(self._color, self.window())
        if color is not None and color.isValid():
            self._color = color
            self._update_style()
            self.color_changed.emit(self.color())

    def _update_style(self) -> None:
        text_color = "#000" if self._color.lightness() > 128 else "#fff"
        self.setText(self._color.name())
        self.setStyleSheet(f"background-color: {self._color.name()}; color: {text_color};")


# ---------------------------------------------------------------------------
# ContextWidgetSpec factory
# ---------------------------------------------------------------------------


def create_context_widget(
    spec: ContextWidgetSpec,
    parent: QWidget | None = None,
) -> QWidget:
    """Create a widget from a ContextWidgetSpec declaration."""

    if spec.kind == "button":
        btn = QPushButton(spec.label, parent)
        if spec.on_clicked:
            btn.clicked.connect(spec.on_clicked)
        return btn
    if spec.kind == "separator":
        sep = QWidget(parent)
        sep.setFixedWidth(1)
        sep.setStyleSheet("background-color: palette(mid);")
        return sep
    if spec.kind == "label":
        return QLabel(spec.label, parent)
    # Fallback
    return QLabel(spec.label or spec.key, parent)
