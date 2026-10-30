"""Explicit native conversion controls inside the existing Document Mode dialog."""

import logging
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

from PySide6.QtCore import QEvent, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget

from loomstroke.core.native_cmyk.document_convert_command import DocumentCmykConvertCommand
from loomstroke.io.bundled_color_profiles import create_print_profile_registry
from loomstroke.ui.print_profile_picker import PrintProfilePicker
from loomstroke.ui.translations import TranslationBinder, translate_with_fallback


class NativeCmykConversionControls(QWidget):
    converted = Signal(int, str)

    def __init__(self, canvas, pages, *, registry=None, source_profile=None, parent=None):
        super().__init__(parent)
        self.canvas, self.pages = canvas, pages
        self.source_profile = source_profile
        self._registered_profile = None
        self._future = None
        self._executor = None
        self._command = None
        self._status_source = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._complete_conversion)
        self.destroyed.connect(self._shutdown)
        self.registry = registry if registry is not None else create_print_profile_registry()
        self._translations = TranslationBinder(self._ts)
        layout = QVBoxLayout(self)
        self.description = QLabel(self)
        self.description.setWordWrap(True)
        self._translations.register(
            self.description.setText, "Convert all pages to editable CMYK with a CMYK ICC profile."
        )
        layout.addWidget(self.description)
        self.picker = PrintProfilePicker(self.registry, parent=self)
        layout.addWidget(self.picker)
        self.bits = QComboBox(self)
        self.bits.addItem(self._ts("8 bit"), 8)
        self.bits.addItem(self._ts("16 bit"), 16)
        layout.addWidget(self.bits)
        self.convert_button = QPushButton(self)
        self._translations.register(self.convert_button.setText, "Convert Document to CMYK")
        self.convert_button.clicked.connect(self.convert)
        layout.addWidget(self.convert_button)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.picker.profile_changed.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _ts(source):
        return translate_with_fallback("NativeCmyk", source)

    def refresh(self):
        self._translations.retranslate()
        self.bits.setItemText(0, self._ts("8 bit"))
        self.bits.setItemText(1, self._ts("16 bit"))
        if self._status_source:
            self.status.setText(self._ts(self._status_source))
        available = (
            self.canvas is not None
            and hasattr(self.canvas, "native_cmyk")
            and self.pages is not None
            and hasattr(self.pages, "_book_history")
        )
        native = self.canvas.native_cmyk().profile() if available else None
        self.convert_button.setEnabled(
            self._future is None
            and available
            and native is None
            and self.picker.profile_id() is not None
        )
        if native is not None:
            if native != self._registered_profile:
                self._registered_profile = native
                self.picker.add_profile_bytes(native)
            self._set_status("This document is already CMYK.")

    def _set_status(self, source):
        self._status_source = source
        self.status.setText(self._ts(source))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "status"):
            self.refresh()

    def convert(self):
        if self._future is not None:
            return
        profile = self.registry.profile_bytes(self.picker.profile_id() or "")
        if profile is None:
            return
        try:
            command = DocumentCmykConvertCommand(
                profile,
                bits=self.bits.currentData(),
                source_profile=self.source_profile,
            )
            if not command.prepare(self.pages):
                raise ValueError("No pages to convert")
            self._command = command
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cmyk-convert")
            self._future = self._executor.submit(command.prepare_pixels)
            self.convert_button.setEnabled(False)
            self._set_status("Preparing CMYK conversion...")
            self._timer.start(10)
        except (ValueError, RuntimeError):
            logging.getLogger(__name__).exception("Native CMYK conversion failed")
            self._show_result(False)

    def _complete_conversion(self):
        if self._future is None or not self._future.done():
            return
        self._timer.stop()
        try:
            self._future.result()
            result = self.pages._book_history.execute(self._command)
        except (ValueError, RuntimeError):
            logging.getLogger(__name__).exception("Native CMYK conversion failed")
            result = False
        finally:
            self._executor.shutdown(wait=False)
            self._executor = None
            self._future = None
            self._command = None
        self._show_result(result)

    def _show_result(self, result):
        self._set_status(
            "Converted to CMYK. Undo restores the previous mode."
            if result
            else (
                "CMYK conversion requires raster layers with supported blends "
                "and no live filters or transforms."
            )
        )
        self.convert_button.setEnabled(not result)
        if result:
            native = next(
                layer.native_cmyk
                for layer in self.canvas._flatten_layers()
                if layer.native_cmyk is not None
            )
            self.converted.emit(native.inks.dtype.itemsize * 8, sha256(native.profile).hexdigest())

    def _shutdown(self):
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
