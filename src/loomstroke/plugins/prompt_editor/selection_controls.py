"""Mutually exclusive, optional selection checkboxes shared by all prompt chips."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from loomstroke.ui.translations import translated_literal_domain

_LABELS = {
    "random": "Random",
    "sequential": "Sequential",
    "combinatorial": "Combinatorial",
}
_HELP = (
    "All unchecked: fixed. List: items; String/Prompt: snippets matching the tag; "
    "Text: {a|b|c}. One choice per generation; combinations advance rightmost first."
)


class PromptSelectionControls(QWidget):
    modeChanged = Signal(str)

    def __init__(self, mode: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.buttons: dict[str, QCheckBox] = {}
        for key in _LABELS:
            button = QCheckBox(self)
            button.setChecked(mode == key)
            button.toggled.connect(lambda checked, value=key: self._toggle(value, checked))
            self.buttons[key] = button
            layout.addWidget(button)
        self._help = QLabel(self)
        self._help.setWordWrap(True)
        self._help.setMaximumWidth(290)
        layout.addWidget(self._help)
        self._retranslate()

    def _toggle(self, mode: str, checked: bool) -> None:
        if checked:
            for key, button in self.buttons.items():
                if key != mode:
                    blocked = button.blockSignals(True)
                    button.setChecked(False)
                    button.blockSignals(blocked)
        self.modeChanged.emit(mode if checked else "fixed")

    def _retranslate(self) -> None:
        for key, button in self.buttons.items():
            button.setText(
                translated_literal_domain(
                    "PromptSelectionControls", _LABELS[key], use_fallback=True
                )
            )
        self._help.setText(
            translated_literal_domain("PromptSelectionControls", _HELP, use_fallback=True)
        )

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()
