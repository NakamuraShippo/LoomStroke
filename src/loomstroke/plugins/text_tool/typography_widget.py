"""Collapsible controls for optional Japanese typesetting on the Font panel."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.japanese_text.typography import normalize_typography
from loomstroke.ui.translations import translated_literal_domain


def _ts(source: str) -> str:
    return translated_literal_domain("TextTypography", source, use_fallback=True)


class TypographyWidget(QWidget):
    """Emit explicit field patches; document/default editing belongs to the tool."""

    typography_changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        super().__init__(parent)
        self._updating = False
        self._values = normalize_typography(None)
        self._diagnostics: list[str] = []
        self.controls: dict[str, QWidget] = {}
        self._labels: list[tuple[QLabel, str]] = []
        self._choices: list[tuple[QComboBox, list[tuple[str, str]]]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QToolButton(self)
        self.heading.setCheckable(True)
        self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.heading)
        self.diagnostics_label = QLabel(self)
        self.diagnostics_label.setWordWrap(True)
        layout.addWidget(self.diagnostics_label)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(160)
        self.scroll.setMaximumHeight(310)
        page = QWidget(self.scroll)
        self.scroll.setWidget(page)
        layout.addWidget(self.scroll)
        form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.preset = self._combo(
            form,
            "preset",
            "Preset",
            [("legacy", "Legacy layout"), ("standard", "Japanese standard"), ("custom", "Custom")],
        )
        self.preset.activated.disconnect()
        self.preset.activated.connect(self._preset_changed)
        self._check(form, "enabled", "Use Japanese typesetting")
        self._check(form, "em_advance", "Use em character advance")
        self._combo(
            form,
            "punctuation_spacing",
            "Punctuation spacing",
            [("standard", "Contextual"), ("full", "Full em"), ("half", "Half em")],
        )
        widths = [("half", "Half em"), ("full", "Full em")]
        self._combo(form, "line_start_opening", "Opening bracket at line start", widths)
        self._combo(form, "line_end_closing", "Closing punctuation at line end", widths)
        self._spin(form, "latin_spacing_em", "Japanese-Latin spacing", 0.0, 1.0)
        self._combo(
            form,
            "kinsoku",
            "Line breaking",
            [("ja_standard", "Japanese prohibitions"), ("none", "None")],
        )
        self._check(form, "hanging", "Hang end punctuation")
        self.custom_rules_button = QPushButton(self)
        self.custom_rules_button.clicked.connect(self._edit_custom_rules)
        form.addRow(self.custom_rules_button)
        self._combo(
            form,
            "line_adjustment",
            "Line adjustment",
            [("compress", "Compress spaces"), ("none", "None"), ("justify", "Justify spaces")],
        )
        self._spin(form, "compression_limit_em", "Maximum compression", 0.0, 1.0)
        self._spin(form, "expansion_limit_em", "Maximum expansion", 0.0, 2.0)
        self._spin(form, "first_line_indent_em", "First-line indent", 0.0, 10.0)
        self._combo(
            form,
            "latin_orientation",
            "Latin in vertical text",
            [("sideways", "Sideways"), ("upright", "Upright")],
        )
        self.heading.toggled.connect(self._expand)
        self._expand(False)
        self.set_typography({}, vertical=False)
        self.refresh_translations()

    def _row(self, form: QFormLayout, key: str, source: str, widget: QWidget) -> None:
        label = QLabel(self)
        label.setWordWrap(True)
        self._labels.append((label, source))
        self.controls[key] = widget
        form.addRow(label, widget)

    def _combo(self, form, key, source, values) -> QComboBox:
        widget = QComboBox(self)
        widget.setMinimumContentsLength(6)
        for value, label in values:
            widget.addItem(label, value)
        self._choices.append((widget, values))
        widget.activated.connect(lambda _: self._emit(key, widget.currentData()))
        self._row(form, key, source, widget)
        return widget

    def _check(self, form, key, source) -> None:
        widget = QCheckBox(self)
        widget.toggled.connect(lambda value: self._emit(key, value))
        self._row(form, key, source, widget)

    def _spin(self, form, key, source, lower, upper) -> None:
        widget = QDoubleSpinBox(self)
        widget.setRange(lower, upper)
        widget.setDecimals(2)
        widget.setSingleStep(0.05)
        widget.setSuffix(" em")
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(lambda value: self._emit(key, value))
        self._row(form, key, source, widget)

    def _emit(self, key: str, value: Any) -> None:
        if not self._updating:
            self.typography_changed.emit({key: value})

    def _edit_custom_rules(self):
        from .custom_rules_dialog import CustomRulesDialog  # noqa: PLC0415

        dialog = CustomRulesDialog(self._values, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._emit("custom_rules", dialog.rules())

    def _preset_changed(self, _index: int) -> None:
        if self._updating:
            return
        preset = self.preset.currentData()
        if preset == "legacy":
            self.typography_changed.emit({"enabled": False})
        elif preset == "standard":
            self.typography_changed.emit(normalize_typography({"enabled": True}))

    def set_typography(self, values: Mapping[str, Any], *, vertical: bool) -> None:
        self._updating = True
        try:
            self._values = normalize_typography(values)
            standard = normalize_typography({"enabled": True})
            if not self._values["enabled"]:
                preset = "legacy"
            elif all(self._values[key] == value for key, value in standard.items()):
                preset = "standard"
            else:
                preset = "custom"
            self.preset.setCurrentIndex(self.preset.findData(preset))
            self.custom_rules_button.setEnabled(self._values["enabled"])
            for key, widget in self.controls.items():
                if key == "preset":
                    continue
                value = self._values[key]
                if isinstance(widget, QComboBox):
                    widget.setCurrentIndex(widget.findData(value))
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(value)
                else:
                    widget.setValue(value)
                enabled = key == "enabled" or self._values["enabled"]
                widget.setEnabled(enabled and (key != "latin_orientation" or vertical))
        finally:
            self._updating = False

    def _expand(self, expanded: bool) -> None:
        self.scroll.setVisible(expanded)
        self.heading.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def set_diagnostics(self, codes: list[str]) -> None:
        self._diagnostics = list(codes)
        sources = []
        for code in self._diagnostics:
            category = code.split(":", 1)[0]
            if category == "shape_overflow":
                source = "Some text cannot fit inside the shape."
            elif category in {"ink_overflow", "line_overflow"}:
                source = "Text extends outside its frame."
            elif category in {"kinsoku_forced_break", "forced_break"}:
                source = "Line breaking could not satisfy the selected prohibitions."
            elif category == "hanging":
                source = "End punctuation hangs outside the line."
            elif category == "annotation_cross_range":
                source = (
                    "Ruby may contain a whole tate-chu-yoko range; "
                    "partial overlap is not supported."
                )
            else:
                source = "Review the text fit and line-breaking settings."
            if source not in sources:
                sources.append(source)
        self.diagnostics_label.setText("\n".join(_ts(source) for source in sources))
        self.diagnostics_label.setVisible(bool(sources))

    def refresh_translations(self) -> None:
        self.custom_rules_button.setText(_ts("Custom line breaking and spacing"))
        self.set_diagnostics(self._diagnostics)
        self.heading.setText(_ts("Japanese typesetting"))
        self.heading.setToolTip(
            _ts("Existing text keeps its layout until these settings are enabled.")
        )
        for label, source in self._labels:
            label.setText(_ts(source))
        for widget, values in self._choices:
            for index, (_, source) in enumerate(values):
                widget.setItemText(index, _ts(source))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
