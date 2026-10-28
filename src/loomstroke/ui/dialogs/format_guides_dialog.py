"""Reapply editable physical-format guides without resizing document pixels."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QWidget,
)

from ...core.book_spread_layout import BookLayout
from ...core.comic_document_profile import BUILTIN_COMIC_PROFILES, PhysicalPageLayout, comic_profile
from ...core.format_guide_service import FormatGuideService
from ...core.page_templates import PageTemplateStore
from ...core.print_format_library import PrintFormat, PrintFormatLibrary
from ..translations import translate_with_fallback

_FIELDS = (
    "Trim width",
    "Trim height",
    "Bleed left",
    "Bleed top",
    "Bleed right",
    "Bleed bottom",
    "Safe left",
    "Safe top",
    "Safe right",
    "Safe bottom",
)
_FAILURE = (
    "Could not apply the page guides. The format must match the existing canvas size "
    "and leave a valid safe area. Some pages may already have been updated."
)


class FormatGuidesDialog(QDialog):
    def __init__(
        self,
        service: FormatGuideService,
        parent: QWidget | None = None,
        *,
        library: PrintFormatLibrary | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.initial = service.current_layout()
        self._error_source = ""
        self.library = library or PrintFormatLibrary(PageTemplateStore())
        self.saved_formats = self.library.formats()
        form = QFormLayout(self)
        self.labels = []
        self.preset = QComboBox(self)
        self.preset.addItem("", "")
        for profile in BUILTIN_COMIC_PROFILES:
            self.preset.addItem(profile.name, profile.id)
        for key, value in self.saved_formats.items():
            self.preset.addItem(value.name, key)
        self._row(form, "Preset", self.preset)
        self.fields = []
        for source in _FIELDS:
            spin = QDoubleSpinBox(self)
            spin.setRange(0, 1000000)
            spin.setDecimals(6)
            spin.setSuffix(" mm")
            self.fields.append(spin)
            self._row(form, source, spin)
        self._set_layout(self.initial)
        self.gutter = QDoubleSpinBox(self)
        self.gutter.setRange(0, 100)
        self.gutter.setDecimals(3)
        self.gutter.setSuffix(" mm")
        self.gutter.setValue(service.binding.current_width_mm())
        self._row(form, "Binding margin", self.gutter)
        self._add_save_controls(form)
        self.all_pages = QCheckBox(self)
        form.addRow(self.all_pages)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        form.addRow(self.note)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        form.addRow(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.preset.currentIndexChanged.connect(self._choose_preset)
        for field in self.fields:
            field.valueChanged.connect(lambda _value: self.preset.setCurrentIndex(0))
        self._retranslate()

    def _add_save_controls(self, form: QFormLayout) -> None:
        self.saved_dpi = []
        for source, value in zip(("Preset DPI X", "Preset DPI Y"), self.initial.dpi, strict=True):
            spin = QDoubleSpinBox(self)
            spin.setRange(1, 10000)
            spin.setDecimals(6)
            spin.setValue(value)
            self.saved_dpi.append(spin)
            self._row(form, source, spin)
        self.save_button = QPushButton(self)
        self.save_button.clicked.connect(self._save_preset)
        form.addRow(self.save_button)

    def _row(self, form: QFormLayout, source: str, widget: QWidget) -> None:
        label = QLabel(self)
        self.labels.append((label, source))
        form.addRow(label, widget)

    def _set_layout(self, layout: PhysicalPageLayout) -> None:
        for spin, value in zip(
            self.fields, (*layout.trim_mm, *layout.bleed_mm, *layout.safe_mm), strict=True
        ):
            blocked = spin.blockSignals(True)
            try:
                spin.setValue(value)
            finally:
                spin.blockSignals(blocked)

    def _choose_preset(self) -> None:
        key = self.preset.currentData()
        if key:
            saved = self.saved_formats.get(key)
            layout = saved.layout if saved else comic_profile(key).layout
            self._set_layout(layout)
            self.gutter.setValue(saved.gutter_mm if saved else 0)
            for spin, value in zip(self.saved_dpi, layout.dpi, strict=True):
                spin.setValue(value)

    def save_preset(self, name: str) -> str:
        layout = replace(self.layout_value(), dpi=tuple(s.value() for s in self.saved_dpi))
        value = PrintFormat(name, layout, self.gutter.value())
        key = self.library.save(value)
        self.saved_formats[key] = value
        self.preset.addItem(value.name, key)
        self.preset.setCurrentIndex(self.preset.count() - 1)
        return key

    def _save_preset(self) -> None:
        name, accepted = QInputDialog.getText(
            self, self._ts("Save Format Preset"), self._ts("Name")
        )
        if not accepted:
            return
        try:
            self.save_preset(name)
        except (ValueError, OSError, RuntimeError):
            self._error_source = "Could not save the format preset."
            self.error.setText(self._ts(self._error_source))

    def layout_value(self) -> PhysicalPageLayout:
        values = [spin.value() for spin in self.fields]
        return PhysicalPageLayout(
            trim_mm=(values[0], values[1]),
            bleed_mm=(values[2], values[3], values[4], values[5]),
            safe_mm=(values[6], values[7], values[8], values[9]),
            dpi=self.initial.dpi,
        )

    def accept(self) -> None:
        try:
            self.service.apply(
                self.layout_value(),
                gutter_mm=self.gutter.value(),
                all_pages=self.all_pages.isChecked(),
            )
        except (ValueError, OSError, RuntimeError):
            self._error_source = _FAILURE
            self.error.setText(self._ts(self._error_source))
            return
        super().accept()

    @staticmethod
    def _ts(source: str) -> str:
        return translate_with_fallback("FormatGuidesDialog", source)

    def _retranslate(self) -> None:
        self.setWindowTitle(self._ts("Page Format Guides"))
        for label, source in self.labels:
            label.setText(self._ts(source))
        self.preset.setItemText(0, self._ts("Custom"))
        for index, profile in enumerate(BUILTIN_COMIC_PROFILES, 1):
            self.preset.setItemText(index, self._ts(profile.name))
        self.all_pages.setText(self._ts("All pages"))
        self.save_button.setText(self._ts("Save Format Preset"))
        self.note.setText(self._ts("Guides use each page's DPI. Canvas size is unchanged."))
        if self._error_source:
            self.error.setText(self._ts(self._error_source))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def open_format_guides(window: Any) -> None:
    pages = getattr(getattr(window, "_host_api", None), "pages", None)
    controller = getattr(getattr(window, "canvas", None), "_guide_controller", None)
    if pages is None or controller is None or controller.guides_locked:
        return
    document = window._get_document_service()
    resolve = getattr(document, "resolve_book_layout", None)
    layout = resolve() if callable(resolve) else BookLayout()
    dialog = FormatGuidesDialog(FormatGuideService(pages, layout), parent=window)
    dialog.exec()
    controller.reload_page_state()
