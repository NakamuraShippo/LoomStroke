"""Plain chip text with matching wrap-aware paint and layout measurements."""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy


class WrappedChipLabel(QLabel):
    """QLabel's normal WordWrap leaves a long unbroken token on one line.

    Use Qt's WrapAnywhere in both measurement and painting so prompt tokens,
    CJK text and URLs all remain inside the available chip width. Source text
    remains untouched and is never interpreted as HTML.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        rect = self.fontMetrics().boundingRect(
            QRect(0, 0, max(1, width), 16777215),
            int(Qt.TextFlag.TextWrapAnywhere | Qt.TextFlag.TextExpandTabs),
            self.text(),
        )
        return max(self.fontMetrics().height(), rect.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(1, self.fontMetrics().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        width = max(
            (self.fontMetrics().horizontalAdvance(s) for s in self.text().splitlines()), default=1
        )
        return QSize(max(1, width), self.heightForWidth(max(1, width)))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(
            self.contentsRect(),
            int(self.alignment()) | int(Qt.TextFlag.TextWrapAnywhere | Qt.TextFlag.TextExpandTabs),
            self.text(),
        )
