"""Serializable edge-local erase gestures with ordinary display-mask coverage."""

import copy
import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

from .membership import PART_KEY
from .models import PanelFrameError


def edge_coordinates(point, endpoints):
    a, b = endpoints
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 1e-8:
        raise PanelFrameError("invalid vector erase boundary")
    return ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length**2, (
        (point[1] - a[1]) * dx - (point[0] - a[0]) * dy
    ) / length


def page_coordinates(point, endpoints):
    a, b = endpoints
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    t, normal = point
    return a[0] + t * dx - normal * dy / length, a[1] + t * dy + normal * dx / length


def append_erasure(metadata, endpoints, points, width):
    if not points or len(points) > 8192 or not math.isfinite(width) or width <= 0:
        raise PanelFrameError("invalid vector erase gesture")
    if not all(math.isfinite(v) for p in points for v in p):
        raise PanelFrameError("invalid vector erase points")
    result = copy.deepcopy(metadata)
    result[PART_KEY].setdefault("erase_strokes", []).append(
        {"points": [list(edge_coordinates(p, endpoints)) for p in points], "width": width}
    )
    return result


def rebase_erasure(metadata, old, new):
    result = copy.deepcopy(metadata)
    for stroke in result.get(PART_KEY, {}).get("erase_strokes", []):
        stroke["points"] = [
            list(edge_coordinates(page_coordinates(p, old), new)) for p in stroke["points"]
        ]
    return result


def erased_edge_mask(mask, metadata, endpoints, page_size):
    strokes = metadata.get(PART_KEY, {}).get("erase_strokes", [])
    if not strokes:
        return mask
    width, height = map(int, page_size)
    result = (
        mask.convertToFormat(QImage.Format.Format_Alpha8)
        if mask is not None
        else QImage(width, height, QImage.Format.Format_Alpha8)
    )
    if mask is None:
        result.fill(255)
    rows = np.frombuffer(result.bits(), np.uint8).reshape(height, result.bytesPerLine())
    for stroke in strokes:
        points = [page_coordinates(p, endpoints) for p in stroke["points"]]
        radius = stroke["width"] / 2 + 2
        x0 = max(0, math.floor(min(p[0] for p in points) - radius))
        y0 = max(0, math.floor(min(p[1] for p in points) - radius))
        x1 = min(width, math.ceil(max(p[0] for p in points) + radius))
        y1 = min(height, math.ceil(max(p[1] for p in points) + radius))
        if x0 >= x1 or y0 >= y1:
            continue
        coverage = QImage(x1 - x0, y1 - y0, QImage.Format.Format_ARGB32_Premultiplied)
        coverage.fill(0)
        painter = QPainter(coverage)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                QColor(255, 255, 255),
                stroke["width"],
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        path = QPainterPath()
        path.moveTo(points[0][0] - x0, points[0][1] - y0)
        for x, y in points[1:]:
            path.lineTo(x - x0, y - y0)
        if len(points) == 1:
            painter.drawPoint(path.currentPosition())
        else:
            painter.drawPath(path)
        painter.end()
        alpha = coverage.convertToFormat(QImage.Format.Format_Alpha8)
        erased = np.frombuffer(alpha.constBits(), np.uint8).reshape(y1 - y0, alpha.bytesPerLine())[
            :, : x1 - x0
        ]
        target = rows[y0:y1, x0:x1]
        target[:] = (
            (target.astype(np.uint16) * (255 - erased.astype(np.uint16)) + 127) // 255
        ).astype(np.uint8)
    return result
