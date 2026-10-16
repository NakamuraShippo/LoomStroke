"""``File > Place Image…``: choose Link (external file) or Embed (copy into document)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.linked_assets import ResourceMode
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["PlaceModeDialog"]


def _ts(text: str) -> str:
    return translated_literal_domain("LinksPanel", text, use_fallback=True)


class PlaceModeDialog(QDialog):
    def __init__(self, file_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_ts("Place Image"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_ts("How should {name} be placed?").format(name=file_name)))
        self.link_button = QRadioButton(_ts("Link to the file (stays external, can be reloaded)"))
        self.embed_button = QRadioButton(_ts("Embed a copy in the document"))
        self.link_button.setChecked(True)
        layout.addWidget(self.link_button)
        layout.addWidget(self.embed_button)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mode(self) -> ResourceMode:
        return "embedded" if self.embed_button.isChecked() else "linked"

    @classmethod
    def ask(cls, file_name: str, parent: QWidget | None = None) -> ResourceMode | None:
        dialog = cls(file_name, parent)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            return dialog.mode()
        finally:
            dialog.deleteLater()
