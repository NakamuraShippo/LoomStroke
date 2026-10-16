"""Canvas-space controls painted through the generic tool overlay."""

from __future__ import annotations

import math
from itertools import pairwise

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen


def draw(painter: QPainter, curves: dict, selected: tuple | None) -> None:
    painter.save()
    try:
        scale = math.hypot(painter.transform().m11(), painter.transform().m12())
        unit = 1.0 / max(0.01, scale)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for lane, anchors in curves.items():
            if not anchors:
                continue
            path = QPainterPath(QPointF(*anchors[0].pos))
            for a, b in pairwise(anchors):
                path.cubicTo(QPointF(*a.outgoing), QPointF(*b.incoming), QPointF(*b.pos))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 220), 3 * unit))
            painter.drawPath(path)
            painter.setPen(QPen(QColor(40, 130, 230), unit))
            painter.drawPath(path)
            for index, anchor in enumerate(anchors):
                active = selected is not None and selected[:2] == (lane, index)
                if active:
                    for part in ("incoming", "outgoing"):
                        if (part == "incoming" and index == 0) or (
                            part == "outgoing" and index == len(anchors) - 1
                        ):
                            continue
                        position = QPointF(*getattr(anchor, part))
                        painter.setPen(QPen(QColor(40, 130, 230), unit))
                        painter.drawLine(QPointF(*anchor.pos), position)
                        painter.setBrush(QColor(255, 255, 255))
                        painter.drawEllipse(position, 3 * unit, 3 * unit)
                painter.setPen(QPen(QColor(40, 130, 230), unit))
                painter.setBrush(QColor(40, 130, 230) if active else QColor(255, 255, 255))
                x, y = anchor.pos
                painter.drawRect(QRectF(x - 3 * unit, y - 3 * unit, 6 * unit, 6 * unit))
    finally:
        painter.restore()
