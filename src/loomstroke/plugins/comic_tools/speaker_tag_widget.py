"""A color tag is a UI label, independent of the rendered text color."""

from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget

from loomstroke.core.japanese_text.speaker_styles import validate_color_tag

from .panel_formatting import _ts


def tag_icon(value):
    if not value:
        return QIcon()
    pixmap = QPixmap(12, 12)
    pixmap.fill(QColor(value))
    return QIcon(pixmap)


class SpeakerTagWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.value = QLineEdit()
        self.value.setPlaceholderText("#RRGGBB")
        self.choose = QPushButton()
        self.clear = QPushButton()
        layout.addWidget(self.value, 1)
        layout.addWidget(self.choose)
        layout.addWidget(self.clear)
        self.choose.clicked.connect(self._pick)
        self.clear.clicked.connect(self.value.clear)
        self.retranslate()

    def _pick(self):
        color = QColorDialog.getColor(
            QColor(self.value.text() or "#808080"), self, _ts("Color tag")
        )
        if color.isValid():
            self.value.setText(color.name())

    def color(self):
        value = self.value.text().strip()
        validate_color_tag(value)
        return value.lower()

    def retranslate(self):
        self.choose.setText(_ts("Choose color"))
        self.clear.setText(_ts("Clear tag"))
