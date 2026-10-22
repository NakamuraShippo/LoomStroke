"""A readable plain-text view of the existing path inline editor's document."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QTextDocument, QTextOption
from PySide6.QtWidgets import QPlainTextDocumentLayout, QPlainTextEdit


class PathTextBodyEditor(QPlainTextEdit):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self._editor = None
        self._syncing = False
        self._owned_document = None
        self.setReadOnly(True)
        self.setMinimumHeight(90)
        self.setMaximumHeight(160)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.cursorPositionChanged.connect(self._to_inline)
        self.selectionChanged.connect(self._to_inline)
        self.textChanged.connect(self.sync_wrap)

    def sync_wrap(self):
        if self._editor is not None:
            # QPlainTextDocumentLayout shares one line width across its views.
            # Limit the invisible input viewport, retaining the canvas widget's
            # geometry and its independent path glyph/hit-test layout.
            right = max(0, self._editor.contentsRect().width() - self.viewport().width())
            self._editor.setViewportMargins(0, 0, right, 0)
            self._editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_wrap()

    def bind(self, editor):
        if self._editor is editor:
            return
        self.detach()
        if editor is None:
            return
        self._syncing = True
        self._editor = editor
        try:
            self.setDocument(editor.document())
        finally:
            self._syncing = False
        if self._owned_document is not None:
            self._owned_document.deleteLater()
            self._owned_document = None
        self.setReadOnly(False)
        editor.cursorPositionChanged.connect(self._from_inline)
        editor.selectionChanged.connect(self._from_inline)
        self._from_inline()
        self.sync_wrap()

    def detach(self):
        if self._editor is None:
            return
        self._editor.cursorPositionChanged.disconnect(self._from_inline)
        self._editor.selectionChanged.disconnect(self._from_inline)
        self._editor.setViewportMargins(0, 0, 0, 0)
        self._editor = None
        text = self.toPlainText()
        document = QTextDocument(self)
        document.setDocumentLayout(QPlainTextDocumentLayout(document))
        document.setPlainText(text)
        self.setDocument(document)
        self._owned_document = document
        self.setReadOnly(True)

    def _to_inline(self):
        if self._editor is None or self._syncing:
            return
        self._syncing = True
        try:
            self._editor.setTextCursor(self.textCursor())
        finally:
            self._syncing = False

    def _from_inline(self):
        if self._editor is None or self._syncing:
            return
        self._syncing = True
        try:
            self.setTextCursor(self._editor.textCursor())
        finally:
            self._syncing = False

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self.isReadOnly():
            self.controller.edit_body()

    def mousePressEvent(self, event):
        self.controller.edit_body()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if self._editor is not None and (
            event.key() == Qt.Key.Key_Escape
            or (
                event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
                and event.modifiers()
                & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            )
        ):
            self._editor.commit_requested.emit()
            canvas = self.controller.tool._canvas_widget()
            if canvas is not None:
                canvas.window().activateWindow()
                canvas.setFocus()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Undo) or event.matches(
            QKeySequence.StandardKey.Redo
        ):
            super().keyPressEvent(event)
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            super().keyPressEvent(event)
        finally:
            cursor.endEditBlock()

    def inputMethodEvent(self, event):
        self._to_inline()
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            super().inputMethodEvent(event)
        finally:
            cursor.endEditBlock()
        self._to_inline()
        if self._editor is not None:
            self._editor.record_preedit(event)
