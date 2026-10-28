"""Guide library controls; geometry and persistence are delegated to the service."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ...core.guide_template_service import GuideTemplateService
from ...core.page_templates import PageTemplateStore
from ..translations import translate_with_fallback


class GuideTemplatesDialog(QDialog):
    def __init__(
        self, service: GuideTemplateService, *, locked: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.service = service
        form = QFormLayout(self)
        self.name = QLineEdit(self)
        self.save_button = QPushButton(self)
        self.name_label = QLabel(self)
        form.addRow(self.name_label, self.name)
        form.addRow(self.save_button)
        self.templates = QComboBox(self)
        self.template_label = QLabel(self)
        form.addRow(self.template_label, self.templates)
        self.target = QComboBox(self)
        self.target.addItem("", "*")
        for page in service.pages.list_pages():
            self.target.addItem(page.name, page.id)
        self.target.setCurrentIndex(
            max(0, self.target.findData(service.pages.get_active_page_id()))
        )
        self.target_label = QLabel(self)
        form.addRow(self.target_label, self.target)
        self.proportional = QCheckBox(self)
        self.replace = QCheckBox(self)
        self.replace.setEnabled(not locked)
        form.addRow(self.proportional)
        form.addRow(self.replace)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        form.addRow(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close, self
        )
        form.addRow(self.buttons)
        self.save_button.clicked.connect(self._save)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        self._error_source = ""
        self._refresh_templates()
        self._retranslate()

    def _refresh_templates(self, selected: str | None = None) -> None:
        self.templates.clear()
        for template in self.service.templates():
            self.templates.addItem(template.name, template.id)
        if selected is not None:
            self.templates.setCurrentIndex(self.templates.findData(selected))
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(
            self.templates.count() > 0
        )

    def _save(self) -> None:
        try:
            template = self.service.save(self.name.text())
        except (OSError, ValueError, RuntimeError):
            self._error_source = "Could not save the guide template."
            self._retranslate()
            return
        self._error_source = ""
        self._refresh_templates(template.id)
        self._retranslate()

    def _apply(self) -> None:
        ids = (
            [p.id for p in self.service.pages.list_pages()]
            if self.target.currentData() == "*"
            else [self.target.currentData()]
        )
        try:
            self.service.apply(
                self.templates.currentData(),
                ids,
                proportional=self.proportional.isChecked(),
                replace=self.replace.isChecked(),
            )
        except (OSError, ValueError, RuntimeError):
            self._error_source = "Could not apply guides. Some pages may already have been updated."
            self._retranslate()
            return
        self.accept()

    def _retranslate(self) -> None:
        def ts(source: str) -> str:
            return translate_with_fallback("GuideTemplatesDialog", source)

        self.setWindowTitle(ts("Guide Templates"))
        for widget, source in (
            (self.name_label, "Name"),
            (self.save_button, "Save current guides"),
            (self.template_label, "Template"),
            (self.target_label, "Target page"),
            (self.proportional, "Scale positions proportionally"),
            (self.replace, "Replace existing guides"),
        ):
            widget.setText(ts(source))
        self.target.setItemText(0, ts("All pages"))
        self.error.setText(ts(self._error_source) if self._error_source else "")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def open_guide_templates(window: Any) -> None:
    host = getattr(window, "_host_api", None)
    pages = getattr(host, "pages", None)
    if pages is None:
        return
    controller = getattr(getattr(window, "canvas", None), "_guide_controller", None)
    service = GuideTemplateService(PageTemplateStore(), pages)
    dialog = GuideTemplatesDialog(
        service, locked=bool(controller and controller.guides_locked), parent=window
    )
    dialog.exec()
    # Partial application may have changed the active page even after an error.
    if controller is not None:
        controller.reload_page_state()
