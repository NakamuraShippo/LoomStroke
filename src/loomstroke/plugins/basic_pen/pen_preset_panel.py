"""Pen settings and named preset controls for the Brush Manager pen tab."""

from __future__ import annotations

from functools import partial
from typing import Any, cast

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.pen_settings_sections import PEN_SETTING_SECTIONS, pen_section_for_key
from loomstroke.ui.translations import TranslationBinder, translated_literal_domain
from loomstroke.ui.widgets.responsive_button_grid import ResponsiveButtonGrid
from loomstroke.ui.widgets.settings_section import SettingsSection

from .pen_preset import MAX_NAME_LENGTH, PenPreset
from .pen_preset_list_widget import PenPresetListWidget as _PresetListWidget
from .pen_pressure_panel import PenPressurePanel
from .pen_taper_editor import PenTaperEditor

# Percentages are presentation units; emitted and synchronized settings use 0..1.
_NUMERIC_CONTROLS = (
    ("taper_start_length", "Taper In Length (0: off)", 0.0, 2000.0, 0.0, 1.0, " px"),
    ("taper_end_length", "Taper Out Length (0: off)", 0.0, 2000.0, 0.0, 1.0, " px"),
    ("size", "Size", 0.1, 1000.0, 5.0, 1.0, " px"),
    ("hardness", "Hardness", 0.0, 1.0, 1.0, 100.0, " %"),
    ("opacity", "Opacity", 0.0, 1.0, 1.0, 100.0, " %"),
    ("flow", "Flow", 0.0, 1.0, 1.0, 100.0, " %"),
    ("stamp_density", "Stamp Density", 0.0, 1.0, 0.6, 100.0, " %"),
    ("smoothing", "Smoothing", 0.0, 1.0, 0.0, 100.0, " %"),
    ("tone_range", "Tone Range", 0.0, 1.0, 1.0, 100.0, " %"),
    ("tone_gamma", "Tone Gamma", 0.1, 2.0, 0.6, 1.0, ""),
    (
        "recontour_smoothness",
        "Recontour Smoothness (next stroke)",
        0.0,
        100.0,
        30.0,
        1.0,
        " %",
    ),
)
_BOOLEAN_CONTROLS = (
    ("pressure_sensitive", "Pressure Sensitive", True),
    ("tilt_angle", "Tilt Angle", False),
    ("recontour", "Recontour", False),
)


def _ts(source: str) -> str:
    return translated_literal_domain("PenPresets", source, use_fallback=True)


class PenPresetPanel(QWidget):
    """Expose editing requests without owning the tool or preset file I/O."""

    settingsEdited = Signal(dict)
    presetSelected = Signal(str)
    requestSave = Signal()
    requestUpdate = Signal()
    requestImport = Signal()
    requestExport = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controls: dict[str, QDoubleSpinBox | QCheckBox] = {}
        self.labels: dict[str, QLabel] = {}
        self._scales: dict[str, float] = {}
        self.sections: dict[str, SettingsSection] = {}
        self._translations = TranslationBinder()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll)
        content = QWidget(self.scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(6)
        for key, title, _keys in PEN_SETTING_SECTIONS[:4]:
            section = SettingsSection(title, content, expanded=key == "basic")
            self.sections[key] = section
            content_layout.addWidget(section)
        stroke_content = self.sections["stroke"].content
        self.taper_editor = PenTaperEditor(stroke_content)
        self.sections["stroke"].content_layout.addWidget(self.taper_editor)
        self._create_settings_form()
        self.taper_editor.lengthsEdited.connect(self._taper_edited)
        self.pressure_panel = PenPressurePanel(self.sections["pressure"].content)
        self.sections["pressure"].content_layout.addWidget(self.pressure_panel)
        self.pressure_panel.settingsEdited.connect(self.settingsEdited.emit)
        presets = SettingsSection("Pen Presets", content, expanded=True, domain="PenPresets")
        self.sections["presets"] = presets
        content_layout.addWidget(presets)
        self._create_preset_controls(presets.content, presets.content_layout)
        self.scroll.setWidget(content)
        self.name_edit.textChanged.connect(self._update_buttons)
        self._update_buttons()

    def _create_settings_form(self) -> None:
        forms = {}
        for key, section in self.sections.items():
            form = QFormLayout()
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            section.content_layout.addLayout(form)
            forms[key] = form
        for key, source, minimum, maximum, default, scale, suffix in _NUMERIC_CONTROLS:
            section = self.sections[pen_section_for_key(key)]
            content, form = section.content, forms[pen_section_for_key(key)]
            spin = QDoubleSpinBox(content)
            spin.setObjectName(key)
            spin.setDecimals(2)
            spin.setRange(minimum * scale, maximum * scale)
            spin.setValue(default * scale)
            spin.setSuffix(suffix)
            spin.setSingleStep(1.0 if scale == 100.0 else 0.1)
            spin.setKeyboardTracking(False)
            label = QLabel(content)
            label.setWordWrap(True)
            label.setBuddy(spin)
            self._translations.register(label.setText, _ts(source))
            self._translations.register(spin.setAccessibleName, _ts(source))
            form.addRow(label, spin)
            self.controls[key] = spin
            self.labels[key] = label
            self._scales[key] = scale
            spin.valueChanged.connect(partial(self._setting_edited, key))
        for key, source, default in _BOOLEAN_CONTROLS:
            section = self.sections[pen_section_for_key(key)]
            content, form = section.content, forms[pen_section_for_key(key)]
            check = QCheckBox(content)
            check.setObjectName(key)
            check.setChecked(default)
            label = QLabel(content)
            label.setWordWrap(True)
            label.setBuddy(check)
            self._translations.register(label.setText, _ts(source))
            self._translations.register(check.setAccessibleName, _ts(source))
            form.addRow(label, check)
            self.controls[key] = check
            self.labels[key] = label
            check.toggled.connect(partial(self._setting_edited, key))

    def _create_preset_controls(self, content: QWidget, content_layout: QVBoxLayout) -> None:
        self.name_label = QLabel(content)
        self.name_edit = QLineEdit(content)
        self.name_edit.setMaxLength(MAX_NAME_LENGTH)
        self.name_label.setBuddy(self.name_edit)
        self._translations.register(self.name_label.setText, _ts("Preset Name"))
        self._translations.register(self.name_edit.setAccessibleName, _ts("Preset Name"))
        content_layout.addWidget(self.name_label)
        content_layout.addWidget(self.name_edit)
        self.presets = _PresetListWidget(content)
        self.presets.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.presets.setMinimumHeight(80)
        self._translations.register(self.presets.setAccessibleName, _ts("Pen Presets"))
        self.presets.currentItemChanged.connect(self._preset_selected)
        self.presets.reselected.connect(self._preset_reselected)
        content_layout.addWidget(self.presets, 1)

        buttons = []
        self.save_button = QPushButton(content)
        self.update_button = QPushButton(content)
        self.import_button = QPushButton(content)
        self.export_button = QPushButton(content)
        for button, source, signal in (
            (self.save_button, "Save New", self.requestSave),
            (self.update_button, "Update", self.requestUpdate),
            (self.import_button, "Import", self.requestImport),
            (self.export_button, "Export", self.requestExport),
        ):
            self._translations.register(button.setText, _ts(source))
            button.clicked.connect(signal.emit)
            buttons.append(button)
        self.preset_actions = ResponsiveButtonGrid(buttons, content)
        content_layout.addWidget(self.preset_actions)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(100, 50)

    def _setting_edited(self, key: str, value: float | bool) -> None:
        if key in self._scales:
            value = float(value) / self._scales[key]
        if key in ("taper_start_length", "taper_end_length"):
            self._sync_taper_editor()
        if key == "pressure_sensitive":
            self.pressure_panel.set_settings({key: value})
        self.settingsEdited.emit({key: value})

    def _taper_edited(self, values: dict[str, float]) -> None:
        for key in ("taper_start_length", "taper_end_length"):
            if key in values:
                cast(QDoubleSpinBox, self.controls[key]).setValue(values[key])

    def _sync_taper_editor(self) -> None:
        self.taper_editor.set_lengths(
            cast(QDoubleSpinBox, self.controls["taper_start_length"]).value(),
            cast(QDoubleSpinBox, self.controls["taper_end_length"]).value(),
        )

    def set_settings(self, settings: dict[str, Any]) -> None:
        """Synchronize supported controls without sending editing requests."""
        for key, control in self.controls.items():
            if key not in settings:
                continue
            with QSignalBlocker(control):
                if isinstance(control, QCheckBox):
                    control.setChecked(bool(settings[key]))
                else:
                    control.setValue(float(settings[key]) * self._scales[key])
        self._sync_taper_editor()
        self.pressure_panel.set_settings(settings)

    def set_presets(self, presets: list[PenPreset], selected_id: str | None = None) -> None:
        """Refresh names and IDs while retaining the user's pending preset name."""
        selected_id = selected_id or self.selected_preset_id()
        with QSignalBlocker(self.presets):
            self.presets.clear()
            for preset in presets:
                item = QListWidgetItem(preset.name)
                item.setData(Qt.ItemDataRole.UserRole, preset.id)
                self.presets.addItem(item)
                if preset.id == selected_id:
                    self.presets.setCurrentItem(item)
        self._update_buttons()

    def selected_preset_id(self) -> str | None:
        item = self.presets.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def preset_name(self) -> str:
        return self.name_edit.text().strip()

    def set_selected_preset(self, preset_id: str | None) -> None:
        """Select a preset programmatically and display its stored name."""
        with QSignalBlocker(self.presets):
            self.presets.setCurrentRow(-1)
            for index in range(self.presets.count()):
                item = self.presets.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == preset_id:
                    self.presets.setCurrentItem(item)
                    self.name_edit.setText(item.text())
                    break
        self._update_buttons()

    def _preset_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        self._update_buttons()
        if current is not None:
            self.name_edit.setText(current.text())
            self.presetSelected.emit(current.data(Qt.ItemDataRole.UserRole))

    def _preset_reselected(self, current: QListWidgetItem) -> None:
        self._preset_selected(current, None)

    def _update_buttons(self, _text: str = "") -> None:
        named = bool(self.preset_name())
        selected = self.selected_preset_id() is not None
        self.save_button.setEnabled(named)
        self.update_button.setEnabled(named and selected)
        self.export_button.setEnabled(selected)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._translations.retranslate()
            self.taper_editor.retranslate()
            self.pressure_panel.retranslate()
            for section in self.sections.values():
                section.retranslate()
