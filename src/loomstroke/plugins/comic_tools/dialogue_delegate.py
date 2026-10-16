"""Multiline dialogue editor with live preview and a single final commit."""

import logging

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QMessageBox,
    QStyledItemDelegate,
)

from loomstroke.ui.translations import translated_literal_domain

from .dialogue_editing import begin_edit, commit_edit
from .dialogue_editor import DialogueEditor
from .dialogue_preview import DialoguePreview

logger = logging.getLogger(__name__)


def _ts(source):
    return translated_literal_domain("ComicDialogueEditor", source, use_fallback=True)


def editing_hint():
    return _ts(
        "Double-click dialogue to edit. Enter: new line; Ctrl+Enter: apply; Esc: cancel. "
        "Double-click speaker to change its style. Double-click page or balloon to jump."
    )


class DialogueDelegate(QStyledItemDelegate):
    def __init__(self, tool, refresh, parent=None):
        super().__init__(parent)
        self.tool = tool
        self.refresh = refresh

    def _error(self, message):
        QTimer.singleShot(
            0, lambda: QMessageBox.warning(self.parent(), _ts("Dialogue editing"), message)
        )

    def createEditor(self, parent, option, index):  # noqa: N802
        page_id, balloon_id = index.data(Qt.ItemDataRole.UserRole)
        try:
            edit = begin_edit(self.tool, page_id, balloon_id)
        except Exception:
            logger.exception("Could not open dialogue editor")
            self._error(_ts("Could not open this dialogue. Refresh the list and try again."))
            return None
        editor = DialogueEditor(parent)
        editor.dialogue_edit = edit
        editor.dialogue_preview = DialoguePreview(self.tool, edit)
        editor.previewChanged.connect(lambda text: self._preview(editor, text))
        editor.destroyed.connect(editor.dialogue_preview.close)
        timer = QTimer(editor)
        timer.setInterval(100)
        timer.timeout.connect(editor.dialogue_preview.check_page)
        timer.start()
        return editor

    def _preview(self, editor, text):
        try:
            editor.dialogue_preview.update(text)
        except Exception:
            editor.dialogue_preview.close()
            logger.exception("Could not preview dialogue edit")
            self._error(_ts("The dialogue was not updated. The page or text may have changed."))

    def setEditorData(self, editor, index):  # noqa: N802
        blocked = editor.blockSignals(True)
        editor.setPlainText(editor.dialogue_edit.text)
        editor.blockSignals(blocked)

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802
        rect = option.rect
        rect.setHeight(max(rect.height(), 100))
        editor.setGeometry(rect)

    def setModelData(self, editor, model, index):  # noqa: N802
        editor.dialogue_preview.close()
        try:
            changed = commit_edit(self.tool, editor.dialogue_edit, editor.toPlainText())
        except Exception:
            logger.exception("Could not commit dialogue edit")
            self._error(_ts("The dialogue was not updated. The page or text may have changed."))
            return
        if changed:
            QTimer.singleShot(0, self.refresh)

    def destroyEditor(self, editor, index):  # noqa: N802
        editor.dialogue_preview.close()
        super().destroyEditor(editor, index)

    def eventFilter(self, editor, event):  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            editor.dialogue_preview.close()
        if event.type() == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
                return True
            return False  # Enter belongs to the multiline editor / IME.
        return super().eventFilter(editor, event)
