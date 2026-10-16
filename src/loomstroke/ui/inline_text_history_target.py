"""Locate the canvas text input that temporarily owns Undo/Redo actions."""

from typing import Any

from PySide6.QtWidgets import QPlainTextEdit, QWidget


def inline_text_history_target(window: Any) -> QPlainTextEdit | None:
    canvas = getattr(window, "canvas", None)
    if not isinstance(canvas, QWidget):
        return None
    for editor in canvas.findChildren(QPlainTextEdit):
        if editor.property("loomstroke_inline_text_editor") and editor.isVisible():
            return editor
    return None
