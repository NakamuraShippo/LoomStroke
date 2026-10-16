"""Draft multiple dialogue rows, then commit the whole selection once."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .panel_formatting import _ts
from .speaker_tag_widget import tag_icon


def speaker_combo(styles, parent=None):
    combo = QComboBox(parent)
    combo.setEditable(True)
    combo.addItem("", ("", ""))
    for style in styles:
        speaker = style.get("speaker", {})
        identity = (speaker.get("speaker_id", ""), speaker.get("speaker_name", ""))
        if any(identity):
            combo.addItem(
                tag_icon(style.get("color_tag", "")), identity[1] or identity[0], identity
            )
    return combo


def speaker_identity(combo):
    text = combo.currentText().strip()
    if combo.currentIndex() >= 0 and combo.itemText(combo.currentIndex()) == text:
        return combo.currentData()
    return ("", text)


class DialogueBatchDialog(QDialog):
    def __init__(self, batch, parent=None):
        super().__init__(parent)
        self.batch = batch
        self.editors = []
        layout = QVBoxLayout(self)
        styles = batch.tool.host_api.pages.speaker_styles.read().get("styles", [])
        common = QHBoxLayout()
        self.common_speaker = speaker_combo(styles)
        self.apply_speaker = QPushButton()
        self.apply_speaker.clicked.connect(self._apply_speaker)
        common.addWidget(self.common_speaker, 1)
        common.addWidget(self.apply_speaker)
        layout.addLayout(common)
        substitution = QHBoxLayout()
        self.find, self.replacement = QLineEdit(), QLineEdit()
        self.replace_all = QPushButton()
        self.replace_all.clicked.connect(self._replace_all)
        substitution.addWidget(self.find)
        substitution.addWidget(self.replacement)
        substitution.addWidget(self.replace_all)
        layout.addLayout(substitution)
        self.table = QTableWidget(len(batch.rows), 3)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, row in enumerate(batch.rows):
            page = QTableWidgetItem(row.page_name or row.page_id)
            page.setFlags(page.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(index, 0, page)
            speaker = speaker_combo(styles)
            identity = (row.speaker_id, row.speaker_name)
            choice = speaker.findData(identity)
            if choice < 0:
                speaker.addItem(row.speaker_name or row.speaker_id, identity)
                choice = speaker.count() - 1
            speaker.setCurrentIndex(choice)
            editor = QPlainTextEdit()
            editor.setPlainText(row.text)
            self.table.setCellWidget(index, 1, speaker)
            self.table.setCellWidget(index, 2, editor)
            self.table.setRowHeight(index, 110)
            self.editors.append((speaker, editor))
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.resize(800, 550)
        self.retranslate()

    def _apply_speaker(self):
        identity = speaker_identity(self.common_speaker)
        for speaker, _ in self.editors:
            index = speaker.findData(identity)
            if index < 0:
                speaker.addItem(identity[1] or identity[0], identity)
                index = speaker.count() - 1
            speaker.setCurrentIndex(index)

    def _replace_all(self):
        needle = self.find.text()
        if needle:
            for _, editor in self.editors:
                editor.setPlainText(editor.toPlainText().replace(needle, self.replacement.text()))

    def accept(self):
        changes = []
        for row, (speaker, editor) in zip(self.batch.rows, self.editors, strict=True):
            speaker_id, speaker_name = speaker_identity(speaker)
            changes.append(
                dict(
                    page_id=row.page_id,
                    balloon_id=row.balloon_id,
                    text=editor.toPlainText(),
                    speaker_id=speaker_id,
                    speaker_name=speaker_name,
                )
            )
        try:
            self.batch.commit(changes)
        except (ValueError, KeyError):
            self.status.setText(
                _ts(
                    "The batch was not applied. A dialogue changed or a layer is locked. "
                    "Reopen the editor."
                )
            )
            return
        super().accept()

    def retranslate(self):
        self.setWindowTitle(_ts("Edit selected dialogues"))
        self.apply_speaker.setText(_ts("Apply speaker to all rows"))
        self.find.setPlaceholderText(_ts("Find text"))
        self.replacement.setPlaceholderText(_ts("Replace with"))
        self.replace_all.setText(_ts("Replace in all rows"))
        self.table.setHorizontalHeaderLabels([_ts("Page"), _ts("Speaker"), _ts("Dialogue")])
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(_ts("Apply batch"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_ts("Cancel"))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
