"""Vectorized pixel-centre queries for immutable text mask contours."""

import math

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage


class MaskShapeQueries:
    def __init__(self, mask: QImage, ox: int, oy: int) -> None:
        # Match the existing contour's alpha > 127 rule, including Alpha8.
        self.image = mask.convertToFormat(QImage.Format.Format_Alpha8)
        self.ox, self.oy = ox, oy
        if self.image.isNull():
            self.alpha = np.empty((0, 0), dtype=np.uint8)
            return
        self.alpha = np.frombuffer(self.image.constBits(), dtype=np.uint8).reshape(
            self.image.height(), self.image.bytesPerLine()
        )[:, : self.image.width()]

    def segments(self, x0: float, x1: float, y: float, *, vertical: bool = False):
        low, high = math.floor(min(x0, x1)), math.ceil(max(x0, x1))
        if high <= low:
            return []
        data = self.alpha.T if vertical else self.alpha
        ox, oy = (self.oy, self.ox) if vertical else (self.ox, self.oy)
        row = int(y) - oy
        if not 0 <= row < data.shape[0]:
            return []
        # int(), rather than floor(), preserves the point API at negative coordinates.
        indices = (np.arange(low, high + 1, dtype=np.float64) + 0.5).astype(np.int64) - ox
        valid = (indices >= 0) & (indices < data.shape[1])
        inside = np.zeros(indices.size, dtype=np.bool_)
        inside[valid] = data[row, indices[valid]] > 127
        edges = np.flatnonzero(np.diff(np.r_[False, inside, False]))
        return [
            (float(low + start), float(min(low + stop, high)))
            for start, stop in zip(edges[::2], edges[1::2], strict=True)
            if min(low + stop, high) - (low + start) > 1.5
        ]

    def contains_rect(self, rect: QRectF) -> bool:
        left, right = math.floor(rect.left()), math.ceil(rect.right())
        top, bottom = math.floor(rect.top()), math.ceil(rect.bottom())
        if right <= left or bottom <= top:
            return True
        x0, x1 = int(left + 0.5) - self.ox, int(right - 0.5) - self.ox
        y0, y1 = int(top + 0.5) - self.oy, int(bottom - 0.5) - self.oy
        if x0 < 0 or y0 < 0 or x1 >= self.alpha.shape[1] or y1 >= self.alpha.shape[0]:
            return False
        return bool(np.all(self.alpha[y0 : y1 + 1, x0 : x1 + 1] > 127))
