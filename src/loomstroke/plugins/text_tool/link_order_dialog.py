"""Review selection order before creating a text story."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from loomstroke.core.japanese_text.story_order import OrderedCanvasLink

from .link_picker import picker
from .typography_widget import _ts


def selected_frames(tool):
    if not tool.host_api:
        return []
    canvas = getattr(tool.host_api, "canvas", None)
    selection = getattr(canvas, "_vector_selection", None) or {}
    result = []
    for item in selection.get("items", []):
        hit = tool._find_text_object_by_id(str(item.get("id", "")), layer_id=item.get("layer_id"))
        if hit is not None:
            key = picker(tool).key(hit)
            if key not in [k for k, _ in result]:
                result.append((key, hit.payload.get("content", "")))
    return result


class LinkOrderDialog(QDialog):
    def __init__(self, tool, frames):
        super().__init__(tool._canvas_widget())
        self.tool = tool
        layout = QVBoxLayout(self)
        self.hint = QLabel()
        layout.addWidget(self.hint)
        self.rows = QListWidget()
        for key, text in frames:
            item = QListWidgetItem(str(text).replace("\n", " ")[:60] or _ts("Empty text frame"))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.rows.addItem(item)
        layout.addWidget(self.rows)
        buttons = QHBoxLayout()
        self.up, self.down = QPushButton(), QPushButton()
        self.up.clicked.connect(lambda: self.move(-1))
        self.down.clicked.connect(lambda: self.move(1))
        buttons.addWidget(self.up)
        buttons.addWidget(self.down)
        layout.addLayout(buttons)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.rows.setCurrentRow(0)
        self.refresh_order()
        self.retranslate()

    def keys(self):
        return [
            tuple(self.rows.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.rows.count())
        ]

    def move(self, delta):
        row = self.rows.currentRow()
        if 0 <= row + delta < self.rows.count():
            item = self.rows.takeItem(row)
            self.rows.insertItem(row + delta, item)
            self.rows.setCurrentRow(row + delta)
            self.refresh_order()

    def refresh_order(self):
        for i in range(self.rows.count()):
            item = self.rows.item(i)
            text = item.data(Qt.ItemDataRole.UserRole + 1)
            if text is None:
                text = item.text()
                item.setData(Qt.ItemDataRole.UserRole + 1, text)
            item.setText(f"{i + 1}. {text}")
        state = picker(self.tool)
        state.ordered = self.keys()
        state.show_overlay()

    def accept(self):
        pages = getattr(self.tool.host_api, "pages", None)
        keys = self.keys()
        success = (
            pages.document_stories.link_ordered(keys)
            if pages
            else self.tool.host_api.canvas.submit(OrderedCanvasLink(keys))
        )
        if not success:
            self.status.setText(_ts("The text frames could not be linked."))
            return
        picker(self.tool).show_feedback(keys)
        super().accept()

    def reject(self):
        picker(self.tool).cancel()
        super().reject()

    def retranslate(self):
        self.setWindowTitle(_ts("Link selected frames"))
        self.hint.setText(_ts("Check the numbered frame order before linking."))
        self.hint.setToolTip(_ts("Existing stories keep their internal frame order."))
        self.up.setText(_ts("Move earlier"))
        self.down.setText(_ts("Move later"))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_ts("Link text frames"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_ts("Cancel linking"))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


def link_selection(tool):
    frames = selected_frames(tool)
    if len(frames) < 2:
        tool._show_status(_ts("Select at least two text frames."))
        return
    if tool._inline_editor is not None and tool._inline_editor.preedit_text():
        return
    tool._finish_inline_edit(commit=True)
    picker(tool).cancel()
    LinkOrderDialog(tool, frames).exec()
    tool._properties.sync(force=True)
