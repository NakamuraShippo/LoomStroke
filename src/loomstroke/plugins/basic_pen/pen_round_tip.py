"""The round tip a pen preset carries when no tip image is selected."""

from PySide6.QtGui import QColor, QImage, QPainter


def round_tip_image() -> QImage:
    image = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(255, 255, 255))
    painter.setBrush(QColor(255, 255, 255))
    painter.drawEllipse(1, 1, 126, 126)
    painter.end()
    return image


def is_round_tip(image: QImage) -> bool:
    """Whether a decoded preset tip is that round tip and not an imported shape."""
    reference = round_tip_image()
    return image.size() == reference.size() and image.convertToFormat(reference.format()) == (
        reference
    )
