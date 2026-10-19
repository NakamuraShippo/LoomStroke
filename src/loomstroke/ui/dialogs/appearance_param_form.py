"""Generic parameter editor generated from an ``AppearanceKind`` schema (CT-21 §12)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QWidget,
)

from loomstroke.core.appearance import AppearanceKind, AppearanceParam
from loomstroke.ui.translations import translated_literal_domain

from .appearance_numeric_slider import AppearanceNumericSlider
from .layer_style_lights_editor import LayerStyleLightsEditor
from .layer_style_profile_editor import LayerStyleProfileEditor

__all__ = ["AppearanceParamForm"]

ColorPicker = Callable[[QColor, QWidget], QColor | None]


class AppearanceParamForm(QWidget):
    def __init__(
        self,
        kind: AppearanceKind | None = None,
        parent: QWidget | None = None,
        *,
        color_picker: ColorPicker | None = None,
        on_change: Callable[[], None] | None = None,
        numeric_sliders: bool = False,
    ) -> None:
        super().__init__(parent)
        self._form = QFormLayout(self)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._editors: dict[str, Any] = {}
        self._kind: AppearanceKind | None = None
        self._color_picker = color_picker
        self._numeric_sliders = numeric_sliders
        self._on_change = on_change or (lambda: None)
        self.set_kind(kind)

    @property
    def kind(self) -> AppearanceKind | None:
        return self._kind

    def set_kind(
        self, kind: AppearanceKind | None, values: Mapping[str, Any] | None = None
    ) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)
        self._editors.clear()
        self._kind = kind
        if kind is None:
            return
        merged = dict(kind.defaults())
        merged.update(dict(values or {}))
        for param in kind.params:
            editor = self._editor(param, merged.get(param.key, param.default), merged)
            self._editors[param.key] = editor
            label = param.label or param.key
            if self._numeric_sliders:
                label = translated_literal_domain("AppearancePanel", label, use_fallback=True)
                if isinstance(editor, QWidget):
                    editor.setAccessibleName(label)
            if isinstance(editor, AppearanceNumericSlider):
                editor.slider.setAccessibleName(label)
                editor.spin.setAccessibleName(label)
            self._form.addRow(label, editor if isinstance(editor, QWidget) else editor[0])
            if "lights" in merged and param.key in {"angle", "elevation"}:
                self._form.setRowVisible(self._form.rowCount() - 1, False)

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self._kind is None:
            return out
        for param in self._kind.params:
            out[param.key] = self._read(param, self._editors[param.key])
        return out

    def set_values(self, values: Mapping[str, Any]) -> None:
        if self._kind is not None:
            self.set_kind(self._kind, values)

    # -- editors -----------------------------------------------------------------------

    def _editor(self, param: AppearanceParam, value: Any, values: dict[str, Any]) -> Any:
        if param.kind in {"lights", "curve"}:
            return self._structured_editor(param, value, values)
        if param.kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            box.toggled.connect(lambda _v: self._on_change())
            return box
        if param.kind in ("int", "float"):
            return self._numeric_editor(param, value)
        if param.kind == "enum":
            combo = QComboBox()
            combo.setObjectName(f"{param.key}_choice")
            for choice in param.choices:
                title = (
                    translated_literal_domain("AppearancePanel", choice, use_fallback=True)
                    if self._numeric_sliders
                    else choice
                )
                combo.addItem(title, choice)
            if value in param.choices:
                combo.setCurrentIndex(param.choices.index(value))
            combo.currentIndexChanged.connect(lambda _i: self._on_change())
            return combo
        if param.kind == "color":
            button = QPushButton()
            button.setProperty("color_value", list(value or [0, 0, 0, 255]))
            self._refresh_color_button(button)
            button.clicked.connect(lambda: self._choose_color(button))
            return button
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        spins = []
        for component in value or [0.0, 0.0]:
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(-1e6, 1e6)
            spin.setValue(float(component))
            spin.valueChanged.connect(lambda _v: self._on_change())
            row.addWidget(spin)
            spins.append(spin)
        return holder, spins

    def _structured_editor(
        self, param: AppearanceParam, value: Any, values: dict[str, Any]
    ) -> QWidget:
        if param.kind == "lights":
            return LayerStyleLightsEditor(values, self._on_change, self._color_picker)
        return LayerStyleProfileEditor(value, self._on_change)

    def _numeric_editor(self, param: AppearanceParam, value: Any) -> QWidget:
        if self._numeric_sliders:
            return AppearanceNumericSlider(param, value, self._on_change)
        spin: QSpinBox | QDoubleSpinBox
        if param.kind == "int":
            spin = QSpinBox()
            spin.setRange(
                int(param.minimum if param.minimum is not None else -100000),
                int(param.maximum if param.maximum is not None else 100000),
            )
            spin.setValue(int(value or 0))
        else:
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setSingleStep(float(param.step or 0.1))
            spin.setRange(
                float(param.minimum if param.minimum is not None else -1e6),
                float(param.maximum if param.maximum is not None else 1e6),
            )
            spin.setValue(float(value or 0.0))
        spin.valueChanged.connect(lambda _v: self._on_change())
        return spin

    def _read(self, param: AppearanceParam, editor: Any) -> Any:
        if param.kind in {"lights", "curve"}:
            return editor.value()
        if param.kind == "bool":
            return bool(editor.isChecked())
        if param.kind in ("int", "float"):
            return editor.value()
        if param.kind == "enum":
            return editor.currentData()
        if param.kind == "color":
            return list(editor.property("color_value"))
        _holder, spins = editor
        return [spin.value() for spin in spins]

    def _choose_color(self, button: QPushButton) -> None:
        current = list(button.property("color_value") or [0, 0, 0, 255])
        if self._numeric_sliders and self._color_picker is None:
            dialog = QColorDialog(QColor(*current[:4]), self)
            dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel)
            dialog.currentColorChanged.connect(lambda color: self._set_color(button, color))
            if dialog.exec() != QColorDialog.DialogCode.Accepted:
                self._set_color(button, QColor(*current[:4]))
            dialog.deleteLater()
            return
        picker = self._color_picker or self._pick_color
        picked = picker(QColor(*current[:4]), self)
        if picked is None or not picked.isValid():
            return
        self._set_color(button, picked)

    def _set_color(self, button: QPushButton, picked: QColor) -> None:
        button.setProperty(
            "color_value", [picked.red(), picked.green(), picked.blue(), picked.alpha()]
        )
        self._refresh_color_button(button)
        self._on_change()

    @staticmethod
    def _refresh_color_button(button: QPushButton) -> None:
        value = list(button.property("color_value") or [0, 0, 0, 255])
        color = QColor(*value[:4])
        button.setText(color.name(QColor.NameFormat.HexArgb))
        foreground = "#111111" if color.lightnessF() > 0.55 else "#ffffff"
        button.setStyleSheet(f"background-color: {color.name()}; color: {foreground};")

    @staticmethod
    def _pick_color(current: QColor, parent: QWidget) -> QColor | None:
        color = QColorDialog.getColor(
            current, parent, options=QColorDialog.ColorDialogOption.ShowAlphaChannel
        )
        return color if color.isValid() else None
