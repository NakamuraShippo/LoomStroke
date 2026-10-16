"""Preferences controls for an existing local ComfyUI installation."""

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

from loomstroke.plugins.comfyui.local_runtime import AUTO_START_KEY, FOLDER_KEY, STOP_ON_EXIT_KEY
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
        self.stop_on_exit = QCheckBox(self)
        layout.addWidget(self.stop_on_exit)
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
        self.stop_on_exit.setChecked(bool(get(STOP_ON_EXIT_KEY, False)))

    def save(self) -> None:
        if self.settings is None:
            return
        self.settings.set(FOLDER_KEY, self.folder.text().strip())
        self.settings.set(AUTO_START_KEY, self.auto_start.isChecked())
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
        self.stop_on_exit.setText(tr("Stop ComfyUI when LoomStroke exits"))
        self.hint.setText(
            tr(
                "Uses Local URL below. Requires external services to be enabled. "
                "On exit, only ComfyUI started by this LoomStroke instance can be stopped, "
                "including active generation. Existing and remote servers remain running."
            )
        )

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
