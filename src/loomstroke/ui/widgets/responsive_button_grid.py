"""A small action grid that falls back to one column in narrow docks."""

from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QGridLayout, QLayout, QPushButton, QWidget


class ResponsiveButtonGrid(QWidget):
    def __init__(self, buttons: list[QPushButton], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons = tuple(buttons)
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._reflow()

    @property
    def columns(self) -> int:
        return self._columns

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(
            max((button.minimumSizeHint().width() for button in self._buttons), default=0), 0
        )

    def _reflow(self) -> None:
        widths = [button.minimumSizeHint().width() for button in self._buttons]
        required = max(240, max(widths[::2], default=0) + max(widths[1::2], default=0) + 6)
        columns = 2 if self.width() >= required else 1
        if columns == self._columns:
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, button in enumerate(self._buttons):
            self._grid.addWidget(button, index // columns, index % columns)
        self.updateGeometry()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._reflow()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in (QEvent.Type.LanguageChange, QEvent.Type.FontChange):
            self._reflow()
