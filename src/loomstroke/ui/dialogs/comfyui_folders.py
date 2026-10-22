"""Preferences controls for an existing local ComfyUI installation."""

import os
from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from loomstroke.plugins.comfyui.local_runtime import (
    AUTO_START_KEY,
    FOLDER_KEY,
    SHOW_CONSOLE_KEY,
    STOP_ON_EXIT_KEY,
)
from loomstroke.ui.translations import translate_with_fallback

from .comfyui_status import ComfyUIStatus


class ComfyUIFolders(QGroupBox):
    def __init__(self, settings: Any, parent: Any = None) -> None:
        super().__init__(parent)
        self.settings = settings
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.folder = QLineEdit(self)
        self.browse = QPushButton(self)
        self.browse.clicked.connect(self.choose_folder)
        row.addWidget(self.folder, 1)
        row.addWidget(self.browse)
        layout.addLayout(row)
        self.auto_start = QCheckBox(self)
        layout.addWidget(self.auto_start)
        self.show_console = QCheckBox(self)
        self.show_console.setEnabled(os.name == "nt")
        layout.addWidget(self.show_console)
        self.stop_on_exit = QCheckBox(self)
        layout.addWidget(self.stop_on_exit)
        self.show_console.toggled.connect(self._update_shutdown_option)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.status = ComfyUIStatus(settings, self)
        layout.addWidget(self.status)
        self.load()
        self.retranslate()

    def load(self) -> None:
        get = getattr(self.settings, "get", lambda key, default: default)
        self.folder.setText(str(get(FOLDER_KEY, "") or ""))
        self.auto_start.setChecked(bool(get(AUTO_START_KEY, False)))
        self.show_console.setChecked(bool(get(SHOW_CONSOLE_KEY, False)))
        self.stop_on_exit.setChecked(bool(get(STOP_ON_EXIT_KEY, False)))
        self._update_shutdown_option()

    def _update_shutdown_option(self) -> None:
        self.stop_on_exit.setEnabled(
            self.show_console.isEnabled() and self.show_console.isChecked()
        )

    def save(self) -> None:
        if self.settings is None:
            return
        self.settings.set(FOLDER_KEY, self.folder.text().strip())
        self.settings.set(AUTO_START_KEY, self.auto_start.isChecked())
        self.settings.set(SHOW_CONSOLE_KEY, self.show_console.isChecked())
        self.settings.set(STOP_ON_EXIT_KEY, self.stop_on_exit.isChecked())

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.title(), self.folder.text())
        if path:
            self.folder.setText(path)

    def retranslate(self) -> None:
        def tr(text: str) -> str:
            return translate_with_fallback("NativeComfyUI", text)

        self.setTitle(tr("ComfyUI Folders"))
        self.folder.setPlaceholderText(tr("ComfyUI folder containing main.py"))
        self.browse.setText(tr("Browse…"))
        self.auto_start.setText(tr("Start ComfyUI when LoomStroke starts"))
        self.show_console.setText(tr("Show ComfyUI console window"))
        self.show_console.setToolTip(
            tr(
                "Windows only. Applies to the next automatic startup. "
                "Shows ComfyUI output in a separate console window instead of the startup log. "
                "Closing the console stops ComfyUI."
            )
        )
        self.stop_on_exit.setText(tr("Stop ComfyUI when LoomStroke exits"))
        self.hint.setText(
            tr(
                "Uses Local URL below. Requires external services to be enabled. "
                "Background ComfyUI started by LoomStroke is automatically stopped on exit, "
                "including active generation. The stop option applies to console startup. "
                "Existing and remote servers remain running."
            )
        )

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
