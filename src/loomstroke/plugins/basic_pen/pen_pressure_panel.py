"""Two pressure editors and numeric fields, independent of tool and file I/O."""

from __future__ import annotations

from functools import partial
from typing import Any

from PySide6.QtCore import QEvent, QSignalBlocker, Signal
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QLabel, QVBoxLayout, QWidget

from loomstroke.ui.translations import TranslationBinder, translated_literal_domain

from .pen_pressure_editor import PenPressureEditor
from .pressure_response_settings import PressureResponseSettings


def _ts(source: str) -> str:
    return translated_literal_domain("BasicPen", source, use_fallback=True)


class PenPressurePanel(QWidget):
    """Emit one patch per gesture update; external synchronization is silent."""

    settingsEdited = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = PressureResponseSettings()
        self._pressure_sensitive = True
        self._translations = TranslationBinder()
        self.controls: dict[str, QDoubleSpinBox | QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.size_editor = PenPressureEditor("Size Pressure Curve", self)
        self.opacity_editor = PenPressureEditor("Opacity Pressure Curve", self)
        check = QCheckBox(self)
        self._translations.register(check.setText, _ts("Opacity Pressure"))
        self.controls["opacity_pressure_enabled"] = check
        layout.addWidget(self.size_editor)
        self._create_numbers(layout, "size")
        layout.addWidget(check)
        layout.addWidget(self.opacity_editor)
        self._create_numbers(layout, "opacity")
        check.toggled.connect(lambda value: self._edit({"opacity_pressure_enabled": value}))
        self.size_editor.responseEdited.connect(partial(self._graph_edited, "size"))
        self.opacity_editor.responseEdited.connect(partial(self._graph_edited, "opacity"))
        self.set_settings({})

    def _create_numbers(self, layout: QVBoxLayout, prefix: str) -> None:
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        labels = (
            ("gamma", "Size Pressure Curve" if prefix == "size" else "Opacity Pressure Curve"),
            (
                "minimum",
                "Minimum Pressure Size" if prefix == "size" else "Minimum Pressure Opacity",
            ),
        )
        for kind, source in labels:
            key = f"{prefix}_pressure_{kind}"
            spin = QDoubleSpinBox(self)
            spin.setObjectName(key)
            spin.setDecimals(2)
            spin.setRange(0.1, 4.0) if kind == "gamma" else spin.setRange(0, 100)
            spin.setSingleStep(0.05 if kind == "gamma" else 1)
            spin.setKeyboardTracking(False)
            if kind == "minimum":
                spin.setSuffix(" %")
            label = QLabel(self)
            label.setWordWrap(True)
            label.setBuddy(spin)
            self._translations.register(label.setText, _ts(source))
            self._translations.register(spin.setAccessibleName, _ts(source))
            form.addRow(label, spin)
            self.controls[key] = spin
            spin.valueChanged.connect(partial(self._number_edited, key))

    def _number_edited(self, key: str, value: float) -> None:
        self._edit({key: value / 100 if key.endswith("minimum") else value})

    def _graph_edited(self, prefix: str, values: dict[str, float]) -> None:
        self._edit({f"{prefix}_pressure_{key}": value for key, value in values.items()})

    def _edit(self, values: dict[str, Any]) -> None:
        self.set_settings(values)
        self.settingsEdited.emit({key: self._settings.values()[key] for key in values})

    def set_settings(self, values: dict[str, Any]) -> None:
        self._settings.update(values)
        if "pressure_sensitive" in values:
            self._pressure_sensitive = bool(values["pressure_sensitive"])
        for key, value in self._settings.values().items():
            control = self.controls[key]
            with QSignalBlocker(control):
                if isinstance(control, QCheckBox):
                    control.setChecked(value)
                else:
                    control.setValue(value * 100 if key.endswith("minimum") else value)
            enabled = self._pressure_sensitive and (
                not key.startswith("opacity_pressure_")
                or key == "opacity_pressure_enabled"
                or self._settings.opacity_pressure_enabled
            )
            control.setEnabled(enabled)
        for prefix, editor in (("size", self.size_editor), ("opacity", self.opacity_editor)):
            editor.set_response(
                getattr(self._settings, f"{prefix}_pressure_gamma"),
                getattr(self._settings, f"{prefix}_pressure_minimum"),
            )
            editor.setEnabled(
                self._pressure_sensitive
                and (prefix == "size" or self._settings.opacity_pressure_enabled)
            )

    def retranslate(self) -> None:
        self._translations.retranslate()
        self.size_editor.retranslate()
        self.opacity_editor.retranslate()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
