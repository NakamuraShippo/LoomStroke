"""Selection formatting presets on the existing Font panel."""

from uuid import uuid4

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .typography_widget import _ts


class NamedStylesWidget(QWidget):
    requested = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.kind = QComboBox(self)
        self.kind.addItem("", "character")
        self.kind.addItem("", "paragraph")
        self.name = QLineEdit(self)
        self.styles = QComboBox(self)
        layout.addWidget(self.kind)
        layout.addWidget(self.name)
        layout.addWidget(self.styles)
        row = QHBoxLayout()
        self.buttons = {}
        for action, source in [
            ("register", "Register style"),
            ("apply", "Apply style"),
            ("clear", "Clear style"),
            ("delete", "Delete style"),
        ]:
            button = QPushButton(self)
            button.clicked.connect(lambda _checked=False, a=action: self._request(a))
            self.buttons[action] = (button, source)
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh_translations()

    def set_values(self, values):
        selected = self.styles.currentData()
        self.styles.clear()
        for style_id, definition in values.get("named_styles", {}).items():
            self.styles.addItem(definition.get("name", style_id), style_id)
        index = self.styles.findData(selected)
        if index >= 0:
            self.styles.setCurrentIndex(index)
        self.setEnabled(bool(values.get("color_range_selected", False)))

    def _request(self, action):
        if action == "register" and not self.name.text().strip():
            return
        request = {
            "action": action,
            "kind": self.kind.currentData(),
            "id": uuid4().hex if action == "register" else self.styles.currentData(),
            "name": self.name.text().strip(),
        }
        self.requested.emit("named_style", request)

    def refresh_translations(self):
        self.kind.setItemText(0, _ts("Character style"))
        self.kind.setItemText(1, _ts("Paragraph style"))
        self.name.setPlaceholderText(_ts("Style name"))
        self.name.setAccessibleName(_ts("Style name"))
        self.styles.setAccessibleName(_ts("Named styles"))
        for button, source in self.buttons.values():
            button.setText(_ts(source))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
