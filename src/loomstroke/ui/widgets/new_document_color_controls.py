"""Colour-mode and CMYK profile fields for the new-document dialog."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QComboBox, QGroupBox, QLabel, QVBoxLayout

from loomstroke.core.new_document_color_spec import NewDocumentColorSpec
from loomstroke.io.bundled_color_profiles import create_print_profile_registry
from loomstroke.ui.print_profile_picker import PrintProfilePicker
from loomstroke.ui.translations import TranslationBinder, translate_with_fallback


class NewDocumentColorControls(QGroupBox):
    validity_changed = Signal(bool)

    def __init__(self, parent=None, *, registry=None):
        super().__init__(parent)
        self.registry = registry if registry is not None else create_print_profile_registry()
        self._binder = TranslationBinder(self._translate)
        self._binder.register(self.setTitle, "Color Mode")
        layout = QVBoxLayout(self)
        self.mode_combo = QComboBox(self)
        for label, mode in (
            ("RGB / 8 bit", "rgba8"),
            ("CMYK / 8 bit", "cmyk8"),
            ("CMYK / 16 bit", "cmyk16"),
        ):
            index = self.mode_combo.count()
            self.mode_combo.addItem(label, mode)
            self._binder.register(lambda text, i=index: self.mode_combo.setItemText(i, text), label)
        layout.addWidget(self.mode_combo)
        self.picker = PrintProfilePicker(self.registry, parent=self)
        layout.addWidget(self.picker)
        self._note = QLabel(self)
        self._note.setWordWrap(True)
        self._binder.register(
            self._note.setText, "Choose a CMYK ICC profile to create a CMYK document."
        )
        layout.addWidget(self._note)
        self.mode_combo.currentIndexChanged.connect(self._refresh)
        self.picker.profile_changed.connect(self._refresh)
        self._refresh()

    def is_valid(self) -> bool:
        return self.mode_combo.currentData() == "rgba8" or self.picker.profile_id() is not None

    def color_spec(self) -> NewDocumentColorSpec:
        mode = self.mode_combo.currentData()
        if mode == "rgba8":
            return NewDocumentColorSpec()
        profile = self.registry.profile_bytes(self.picker.profile_id() or "")
        return NewDocumentColorSpec(mode, profile)

    def _refresh(self, *_args) -> None:
        cmyk = self.mode_combo.currentData() != "rgba8"
        self.picker.setVisible(cmyk)
        self._note.setVisible(cmyk)
        self.validity_changed.emit(self.is_valid())

    def refresh_translations(self) -> None:
        self._binder.retranslate()
        self.picker.refresh_translations()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "picker"):
            self.refresh_translations()
        super().changeEvent(event)

    @staticmethod
    def _translate(text: str) -> str:
        return translate_with_fallback("NewDocumentColor", text)
