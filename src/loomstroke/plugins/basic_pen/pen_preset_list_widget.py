"""A pen preset list that can reapply the already selected preset."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QListWidget


class PenPresetListWidget(QListWidget):
    reselected = Signal(object)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        item = self.itemAt(event.position().toPoint())
        same_item = item is not None and item == self.currentItem()
        super().mousePressEvent(event)
        if same_item and event.button() == Qt.MouseButton.LeftButton and item.isSelected():
            self.reselected.emit(item)
