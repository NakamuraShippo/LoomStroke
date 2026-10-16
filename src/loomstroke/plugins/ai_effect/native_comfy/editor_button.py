"""Entry point from the preset Editor to native ComfyUI workflow editing."""

from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QPushButton

from .panel import tr


class WorkflowEditButton(QPushButton):
    def __init__(self, editor: Any) -> None:
        super().__init__(editor)
        self.editor = editor
        self.setVisible(False)  # The Manager supplies the native workflow host.
        self.setEnabled(False)
        self.retranslate()

    def refresh(self) -> None:
        gateway = self.editor.current_gateway()
        self.setEnabled(
            getattr(gateway, "gateway_id", None) == "comfyui"
            and bool(self.editor.current_workflow())
        )

    def retranslate(self) -> None:
        self.setText(tr("Edit workflow in ComfyUI"))
        self.setToolTip(tr("Open this effect's workflow in the ComfyUI tab."))

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
