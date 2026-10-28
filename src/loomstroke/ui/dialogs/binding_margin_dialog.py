"""Binding-margin controls without changing book metadata or page format values."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QWidget,
)

from ...core.binding_margin_service import BindingMarginService
from ...core.book_spread_layout import BookLayout
from ..translations import translate_with_fallback

_FAILURE_SOURCE = (
    "Could not apply the binding margin. Check the page format; "
    "some pages may already have been updated."
)


class BindingMarginDialog(QDialog):
    def __init__(
        self, service: BindingMarginService, parent: QWidget | None = None, *, settings: Any = None
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.settings = settings
        form = QFormLayout(self)
        self.width_mm = QDoubleSpinBox(self)
        self.width_mm.setRange(0, 100)
        self.width_mm.setDecimals(3)
        self.width_mm.setValue(service.current_width_mm())
        self.width_label = QLabel(self)
        form.addRow(self.width_label, self.width_mm)
        self.all_pages = QCheckBox(self)
        form.addRow(self.all_pages)
        self.show_danger = QCheckBox(self)
        self.show_danger.setChecked(
            bool(settings.get("view/spread_binding_overlay", False))
            if settings is not None
            else False
        )
        self.show_danger.setEnabled(settings is not None)
        form.addRow(self.show_danger)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        form.addRow(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self._retranslate()

    def accept(self) -> None:
        try:
            self.service.apply(self.width_mm.value(), all_pages=self.all_pages.isChecked())
        except (ValueError, OSError, RuntimeError):
            self.error.setText(self._ts(_FAILURE_SOURCE))
            return
        if self.settings is not None:
            self.settings.set("view/spread_binding_overlay", self.show_danger.isChecked())
        super().accept()

    @staticmethod
    def _ts(source: str) -> str:
        return translate_with_fallback("BindingMarginDialog", source)

    def _retranslate(self) -> None:
        self.setWindowTitle(self._ts("Binding Margin"))
        self.width_label.setText(self._ts("Width (mm)"))
        self.all_pages.setText(self._ts("All pages"))
        self.show_danger.setText(self._ts("Show binding danger zone in spread view"))
        if self.error.text():
            self.error.setText(self._ts(_FAILURE_SOURCE))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def open_binding_margin(window: Any) -> None:
    pages = getattr(getattr(window, "_host_api", None), "pages", None)
    controller = getattr(getattr(window, "canvas", None), "_guide_controller", None)
    if pages is None or controller is None or controller.guides_locked:
        return
    document = window._get_document_service()
    resolve = getattr(document, "resolve_book_layout", None)
    layout = resolve() if callable(resolve) else BookLayout()
    dialog = BindingMarginDialog(
        BindingMarginService(pages, layout), parent=window, settings=window._settings
    )
    dialog.exec()
    controller.reload_page_state()
