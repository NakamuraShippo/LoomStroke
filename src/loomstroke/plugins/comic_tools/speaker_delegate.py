"""Dialogue speaker edits assign identity and inherited style in one command."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate

from .speaker_tag_widget import tag_icon


class SpeakerDelegate(QStyledItemDelegate):
    def __init__(self, tool, refresh, parent=None):
        super().__init__(parent)
        self.tool, self.refresh = tool, refresh

    def createEditor(self, parent, option, index):
        page_id, balloon_id = index.data(Qt.ItemDataRole.UserRole)
        self.tool.jump_to_balloon(page_id, balloon_id)
        editor = QComboBox(parent)
        editor.setEditable(True)
        editor.speaker_target = (page_id, balloon_id)
        service = getattr(self.tool.host_api.pages, "speaker_styles", None)
        for style in service.read().get("styles", []) if service else []:
            speaker = style.get("speaker", {})
            identity = (speaker.get("speaker_id", ""), speaker.get("speaker_name", ""))
            if any(identity):
                editor.addItem(
                    tag_icon(style.get("color_tag", "")), identity[1] or identity[0], identity
                )
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data() or ""))

    def setModelData(self, editor, model, index):
        page_id, balloon_id = editor.speaker_target
        if self.tool.host_api.pages.get_active_page_id() != page_id:
            return
        text = editor.currentText().strip()
        identity = (
            editor.currentData()
            if editor.currentIndex() >= 0 and editor.itemText(editor.currentIndex()) == text
            else ("", text)
        )
        if self.tool.set_balloon_speaker(balloon_id, *identity):
            QTimer.singleShot(0, self.refresh)
