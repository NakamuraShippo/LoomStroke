"""Build document-coordinate selection contours without changing canvas state."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainterPath


class SelectionShapePath:
    @staticmethod
    def build(
        mode: str,
        points: Sequence[tuple[float, float]],
        *,
        square: bool = False,
        center: bool = False,
        pixel_perfect: bool = False,
    ) -> QPainterPath:
        path = QPainterPath()
        if not points or any(not math.isfinite(v) for p in points for v in p):
            return path
        path.setFillRule(Qt.FillRule.OddEvenFill)
        if mode == "Ellipse" and len(points) >= 2:
            x0, y0 = points[0]
            x1, y1 = points[-1]
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            if square:
                dx = dy = max(dx, dy)
            if center:
                left, top, right, bottom = x0 - dx, y0 - dy, x0 + dx, y0 + dy
            else:
                left = x0 if x1 >= x0 else x0 - dx
                top = y0 if y1 >= y0 else y0 - dy
                right, bottom = left + dx, top + dy
            if pixel_perfect:
                left, top, right, bottom = map(round, (left, top, right, bottom))
            if right > left and bottom > top:
                path.addEllipse(QRectF(left, top, right - left, bottom - top))
            return path
        if len(points) < 3:
            return path
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        path.closeSubpath()
        return path
