"""Detached convex coverage using the existing Alpha8 page-mask contract."""

import math

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter, QPolygonF

from .models import PanelFrameError


def validate_polygon(points):
    points = tuple(tuple(p) for p in points)
    if len(points) < 3 or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
        raise PanelFrameError("invalid convex polygon")
    crosses = []
    for a, b, c in zip(points, points[1:] + points[:1], points[2:] + points[:2], strict=True):
        crosses.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    if min(crosses) < -1e-8 or max(crosses) <= 1e-8:
        raise PanelFrameError("polygon must be convex with canvas clockwise winding")
    return points


def inset_polygon(points, inset):
    points = validate_polygon(points)
    if not math.isfinite(inset) or inset < 0:
        raise PanelFrameError("invalid polygon inset")
    # Intersection of neighbouring inward-offset lines; no stored path authority.
    lines = []
    for a, b in zip(points, points[1:] + points[:1], strict=True):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 1e-8:
            raise PanelFrameError("polygon has a zero-length edge")
        normal = (-dy / length, dx / length)
        lines.append((*normal, normal[0] * a[0] + normal[1] * a[1] + inset))
    result = []
    for a, b in zip(lines[-1:] + lines[:-1], lines, strict=True):
        determinant = a[0] * b[1] - a[1] * b[0]
        if abs(determinant) < 1e-10:
            raise PanelFrameError("polygon contains redundant parallel edges")
        result.append(
            ((a[2] * b[1] - a[1] * b[2]) / determinant, (a[0] * b[2] - a[2] * b[0]) / determinant)
        )
    if any(nx * x + ny * y < offset - 1e-7 for x, y in result for nx, ny, offset in lines):
        raise PanelFrameError("inset consumes the polygon")
    return validate_polygon(result)


def polygon_clip_mask(page_size, polygons):
    width, height = page_size
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise PanelFrameError("invalid polygon mask dimensions")
    shapes = tuple(validate_polygon(p) for p in polygons)
    gray = QImage(width, height, QImage.Format.Format_Grayscale8)
    if gray.isNull():
        raise PanelFrameError("cannot allocate polygon mask")
    gray.fill(0)
    painter = QPainter(gray)
    if not painter.isActive():
        raise PanelFrameError("cannot prepare polygon coverage")
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.white)
        for points in shapes:
            painter.drawPolygon(QPolygonF([QPointF(*p) for p in points]))
    finally:
        painter.end()
    result = QImage(width, height, QImage.Format.Format_Alpha8)
    if result.isNull():
        raise PanelFrameError("cannot allocate polygon coverage")
    result.fill(0)
    src = np.frombuffer(gray.constBits(), np.uint8).reshape(height, gray.bytesPerLine())
    dst = np.frombuffer(result.bits(), np.uint8).reshape(height, result.bytesPerLine())
    dst[:, :width] = src[:, :width]
    return result
