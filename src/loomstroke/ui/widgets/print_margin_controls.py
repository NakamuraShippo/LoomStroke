"""Independent opt-in flags for export margin information."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QWidget

from ...core.print_margin_models import PrintMarginOptions
from ..translations import translate_with_fallback

_FLAGS = (
    ("file_name", "File name"),
    ("page_number", "Page number"),
    ("date_time", "Date and time"),
    ("color_mode", "Colour mode"),
    ("color_bar", "Colour bar"),
    ("gray_bar", "Grey bar"),
)


class PrintMarginControls(QGroupBox):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QGridLayout(self)
        self.flags = {}
        for index, (key, _) in enumerate(_FLAGS):
            box = QCheckBox(self)
            box.toggled.connect(self.changed)
            self.flags[key] = box
            layout.addWidget(box, index // 2, index % 2)
        self.refresh_translations()

    def options(self) -> PrintMarginOptions:
        return PrintMarginOptions(**{key: box.isChecked() for key, box in self.flags.items()})

    def refresh_translations(self) -> None:
        self.setTitle(self._ts("Margin information"))
        for key, source in _FLAGS:
            self.flags[key].setText(self._ts(source))

    @staticmethod
    def _ts(source: str) -> str:
        return translate_with_fallback("PrintMarginControls", source)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
