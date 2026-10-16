"""Live connection status for saved ComfyUI server URLs."""

import asyncio
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from loomstroke.plugins.comfyui.runtime_status import statuses
from loomstroke.ui.translations import translate_with_fallback


class ComfyUIStatus(QWidget):
    def __init__(self, settings: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.settings = settings
        self.task: asyncio.Task | None = None
        self.rows: list[tuple[str, str, str, str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.refresh = QPushButton(self)
        self.refresh.clicked.connect(self.check)
        layout.addWidget(self.label)
        layout.addWidget(self.refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.check)
        self.retranslate()

    def check(self) -> None:
        if self.task is not None and not self.task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.task = loop.create_task(self.update_status())

    async def update_status(self) -> None:
        self.rows = await statuses(self.settings)
        self.retranslate()

    def retranslate(self) -> None:
        def tr(text: str) -> str:
            return translate_with_fallback("NativeComfyUI", text)

        self.refresh.setText(tr("Refresh saved server status"))
        self.label.setText(
            "\n".join(
                f"{name}: {tr(state)}\n{url}" + (f"\n{tr(detail)}" if detail else "")
                for name, url, state, detail in self.rows
            )
            or tr("Checking saved server URLs…")
        )

    def showEvent(self, event: Any) -> None:  # noqa: N802
        super().showEvent(event)
        self.timer.start()
        self.check()

    def hideEvent(self, event: Any) -> None:  # noqa: N802
        self.timer.stop()
        if self.task is not None:
            self.task.cancel()
        super().hideEvent(event)

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
