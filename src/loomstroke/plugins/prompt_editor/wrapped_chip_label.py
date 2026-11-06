"""Plain chip text with matching wrap-aware paint and layout measurements."""

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QPainter, QTextLayout, QTextLine, QTextOption
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

    def char_index_at(self, pos: QPoint) -> int:
        """Index of the character painted at ``pos``, or -1 outside the text."""
        text = self.text()
        rect = self.contentsRect()
        if not text or rect.width() <= 0:
            return -1
        # drawText breaks lines at "\n"; QTextLayout needs a line separator.
        layout = QTextLayout(text.replace("\n", "\u2028"), self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(rect.width())
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        top = float(rect.top())
        if self.alignment() & Qt.AlignmentFlag.AlignVCenter:
            top += (rect.height() - height) / 2
        elif self.alignment() & Qt.AlignmentFlag.AlignBottom:
            top += rect.height() - height
        x = pos.x() - rect.left()
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            y = top + line.y()
            if y <= pos.y() < y + line.height():
                if not 0 <= x < line.naturalTextWidth():
                    return -1
                return line.xToCursor(x, QTextLine.CursorPosition.CursorOnCharacter)
        return -1

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(
            self.contentsRect(),
            int(self.alignment()) | int(Qt.TextFlag.TextWrapAnywhere | Qt.TextFlag.TextExpandTabs),
            self.text(),
        )
