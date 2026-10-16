"""Multiline editor exposing committed text plus transient IME composition."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPlainTextEdit


class DialogueEditor(QPlainTextEdit):
    previewChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._composing = False
        self.textChanged.connect(self._text_changed)

    def _text_changed(self):
        if not self._composing:
            self.previewChanged.emit(self.toPlainText())

    def inputMethodEvent(self, event):  # noqa: N802
        self._composing = True
        try:
            super().inputMethodEvent(event)
        finally:
            self._composing = False
        # QTextCursor positions count UTF-16 code units, not Python code points.
        content = self.toPlainText().encode("utf-16-le")
        position = self.textCursor().position() * 2
        text = (
            content[:position].decode("utf-16-le")
            + event.preeditString()
            + content[position:].decode("utf-16-le")
        )
        self.previewChanged.emit(text)
