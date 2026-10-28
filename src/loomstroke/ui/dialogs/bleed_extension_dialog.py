"""Explicit selected-layer bleed extension, committed once through core."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QWidget

from ...core.bleed_extension_service import BleedExtensionService
from ..translations import translate_with_fallback

_FAILURE = (
    "Could not extend bleed. Select an unlocked, untransformed full-canvas RGBA raster layer "
    "and valid trim guides."
)


class BleedExtensionDialog(QDialog):
    def __init__(self, canvas: Any, pages: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas, self.pages = canvas, pages
        self.page_id = pages.get_active_page_id()
        self.layer_id = canvas.get_canvas_state_snapshot().active_layer_id
        form = QFormLayout(self)
        self.help = QLabel(self)
        self.help.setWordWrap(True)
        form.addRow(self.help)
        self.method = QComboBox(self)
        self.method.addItem("", "edge")
        self.method.addItem("", "mirror")
        self.method_label = QLabel(self)
        form.addRow(self.method_label, self.method)
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
        if self.pages.get_active_page_id() != self.page_id or self.layer_id is None:
            self.error.setText(self._ts(_FAILURE))
            return
        guides = (self.pages.get_page_view_state(self.page_id) or {}).get("guides", [])
        try:
            if not isinstance(guides, list) or any(not isinstance(g, dict) for g in guides):
                raise ValueError("Invalid guides")
            command = BleedExtensionService().prepare(
                self.canvas, guides, str(self.method.currentData()), layer_id=self.layer_id
            )
            if command is not None and not self.canvas.submit(command):
                raise RuntimeError("Bleed command rejected")
        except (ValueError, RuntimeError):
            self.error.setText(self._ts(_FAILURE))
            return
        super().accept()

    @staticmethod
    def _ts(source: str) -> str:
        return translate_with_fallback("BleedExtensionDialog", source)

    def _retranslate(self) -> None:
        self.setWindowTitle(self._ts("Extend Bleed"))
        self.help.setText(
            self._ts(
                "Extend only the area outside the trim guides. Pixels inside trim stay unchanged."
            )
        )
        self.method_label.setText(self._ts("Method"))
        self.method.setItemText(0, self._ts("Stretch edge pixels"))
        self.method.setItemText(1, self._ts("Mirror edge pixels"))
        if self.error.text():
            self.error.setText(self._ts(_FAILURE))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def open_bleed_extension(window: Any) -> None:
    host = getattr(window, "_host_api", None)
    canvas, pages = getattr(host, "canvas", None), getattr(host, "pages", None)
    if canvas is None or pages is None:
        return
    BleedExtensionDialog(canvas, pages, window).exec()
