"""History adapter for document background and ICC metadata."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor

from loomstroke.core.commands import Command
from loomstroke.core.events import DocumentChangeEvent


class CanvasPropertyCommand(Command):
    """Keep metadata changes on the existing canvas Command history path."""

    def __init__(self, field: str, value: Any) -> None:
        super().__init__()
        self.field = field
        self.value = QColor(value) if field == "background" else value
        self.old: Any = None

    def apply(self, canvas: Any) -> bool:
        current = (
            canvas._background
            if self.field == "background"
            else getattr(canvas, "color_profile", None)
        )
        if current == self.value:
            return False
        self.old = QColor(current) if self.field == "background" else current
        self._set(canvas, self.value)
        return True

    def revert(self, canvas: Any) -> bool:
        self._set(canvas, self.old)
        return True

    def _set(self, canvas: Any, value: Any) -> None:
        if self.field == "profile":
            canvas.set_color_profile(value)
            return
        canvas._background = QColor(value)
        canvas._invalidate_layer_cache()
        canvas._recompose()
        canvas._emit_doc_changed(
            DocumentChangeEvent(change_type="background", affected_area=None, layer_id=None)
        )
