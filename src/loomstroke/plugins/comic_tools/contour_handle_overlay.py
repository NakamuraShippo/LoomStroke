"""Middle handle overlay for contour rigs; no document mutation."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen


def paint_middle_handle(painter, session, scale, hover):
    middle = session.middle_point()
    if middle is None:
        return
    painter.save()
    pen = QPen(QColor(80, 180, 130, 180))
    pen.setWidthF(1 / scale)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.drawLine(QPointF(*session.root_point()), QPointF(*middle))
    painter.drawLine(QPointF(*middle), QPointF(*session.tip_point()))
    active = session.dragging and session._drag["kind"] == "middle"
    pen.setStyle(Qt.PenStyle.SolidLine)
    pen.setWidthF((3 if active else 2) / scale)
    painter.setPen(pen)
    painter.setBrush(QColor(80, 200, 130, 200 if active else (130 if hover == "middle" else 60)))
    radius = 6 / scale
    painter.drawRect(QRectF(middle[0] - radius, middle[1] - radius, radius * 2, radius * 2))
    painter.restore()
