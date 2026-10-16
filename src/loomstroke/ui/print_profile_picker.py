"""CMYK output profile picker (CT-11 P3, spec 11 §5.2 / §11 / §14).

A combo of the CMYK profiles registered in a :class:`ColorProfileRegistry`
plus a *Load ICC Profile...* button.  Profiles enter through the registry
(validated by LittleCMS, addressed by fingerprint, path never stored);
a non-CMYK file is refused with an inline message instead of silently
producing a mislabelled separation. Default print/conversion registries
include the licensed bundle; explicitly supplied registries are preserved.
The download guide opens official terms pages only on explicit link activation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..io.color_profile_registry import ColorProfileInfo, ColorProfileRegistry
from .dialogs.icc_profile_download_dialog import IccProfileDownloadDialog
from .translations import TranslationBinder, translate_with_fallback, translated_literal

logger = logging.getLogger(__name__)

__all__ = ["PrintProfilePicker"]


class PrintProfilePicker(QWidget):
    """Select (or load) the destination CMYK profile."""

    profile_changed = Signal()

    def __init__(
        self,
        registry: ColorProfileRegistry,
        *,
        file_dialog: Callable[[], Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._file_dialog = file_dialog or (lambda: QFileDialog)
        self._translation_binder = TranslationBinder(self._translate_runtime)

        self._combo = QComboBox(self)
        self._load_button = QPushButton(self)
        self._translation_binder.register(
            self._load_button.setText, self._ts("Load ICC Profile...")
        )
        self._download_button = QPushButton(self)
        self._translation_binder.register_domain(
            "IccProfileDownloads",
            self._download_button.setText,
            "Download Representative ICC Profiles...",
        )
        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._combo, 1)
        row.addWidget(self._load_button)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addLayout(row)
        column.addWidget(self._download_button)
        column.addWidget(self._status_label)

        self._load_button.clicked.connect(self._on_load_clicked)
        self._download_button.clicked.connect(self._on_download_clicked)
        self._combo.currentIndexChanged.connect(lambda _i: self.profile_changed.emit())
        self._rebuild_items()

    # -- API ---------------------------------------------------------------------

    def profile_id(self) -> str | None:
        data = self._combo.currentData()
        return str(data) if data else None

    def profile_name(self) -> str | None:
        info = self._registry.info(self.profile_id() or "")
        return info.name if info else None

    def add_profile_bytes(self, data: bytes) -> ColorProfileInfo | None:
        """Register ``data``; select it when it is CMYK, else report and keep the selection."""
        try:
            info = self._registry.register(data)
        except ValueError:
            logger.warning("print profile rejected", exc_info=True)
            self._status_label.setText(
                self._translate_runtime("The file is not a usable ICC profile.")
            )
            return None
        if info.colorspace != "CMYK":
            self._status_label.setText(
                self._translate_runtime("The profile is not a CMYK profile.")
            )
            return None
        self._status_label.setText("")
        self._rebuild_items(select=info.profile_id)
        return info

    # -- internals ---------------------------------------------------------------

    def _cmyk_profiles(self) -> tuple[ColorProfileInfo, ...]:
        return tuple(info for info in self._registry.list_profiles() if info.colorspace == "CMYK")

    def _rebuild_items(self, *, select: str | None = None) -> None:
        wanted = select or self.profile_id()
        self._combo.blockSignals(True)
        try:
            self._combo.clear()
            for info in self._cmyk_profiles():
                self._combo.addItem(info.name, info.profile_id)
            if self._combo.count() == 0:
                self._combo.addItem(self._translate_runtime("No CMYK profile loaded"), None)
                self._combo.setEnabled(False)
            else:
                self._combo.setEnabled(True)
                index = self._combo.findData(wanted) if wanted else -1
                self._combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._combo.blockSignals(False)
        self.profile_changed.emit()

    def _on_load_clicked(self) -> None:
        path, _selected = self._file_dialog().getOpenFileName(
            self,
            self._translate_runtime("Choose CMYK Profile"),
            "",
            self._translate_runtime("ICC Profiles (*.icc *.icm);;All Files (*.*)"),
        )
        if not path:
            return
        try:
            payload = Path(path).read_bytes()
        except OSError:
            logger.warning("print profile unreadable", exc_info=True)
            self._status_label.setText(
                self._translate_runtime("The file is not a usable ICC profile.")
            )
            return
        self.add_profile_bytes(payload)

    def _on_download_clicked(self) -> None:
        dialog = IccProfileDownloadDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    # -- i18n --------------------------------------------------------------------

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "_combo"):
            self.refresh_translations()
        super().changeEvent(event)

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        if self._combo.count() == 1 and self._combo.itemData(0) is None:
            self._combo.setItemText(0, self._translate_runtime("No CMYK profile loaded"))

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ExportPrintDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)
