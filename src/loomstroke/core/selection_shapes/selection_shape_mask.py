"""Rasterize selection coverage within the canvas-clipped contour bounds."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath

from loomstroke.core.commands import ModifySelectionMaskCommand


class SelectionShapeMask:
    @staticmethod
    def render(
        path: QPainterPath,
        canvas_size: tuple[int, int],
        *,
        feather_radius: int = 0,
        pixel_perfect: bool = False,
    ) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
        width, height = canvas_size
        if path.isEmpty() or width <= 0 or height <= 0:
            return None
        radius = max(0, min(128, feather_radius))
        rect = path.boundingRect()
        x0, y0 = max(0, math.floor(rect.left()) - radius), max(0, math.floor(rect.top()) - radius)
        x1 = min(width, math.ceil(rect.right()) + radius)
        y1 = min(height, math.ceil(rect.bottom()) + radius)
        if x1 <= x0 or y1 <= y0:
            return None
        image = QImage(
            x1 - x0 + 2 * radius, y1 - y0 + 2 * radius, QImage.Format.Format_ARGB32_Premultiplied
        )
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not pixel_perfect)
        painter.translate(-x0 + radius, -y0 + radius)
        painter.fillPath(path, Qt.GlobalColor.white)
        painter.end()
        alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
        mask = (
            np.frombuffer(alpha.constBits(), np.uint8)
            .reshape(alpha.height(), alpha.bytesPerLine())[:, : alpha.width()]
            .copy()
        )
        if radius:
            # Include off-canvas coverage, so canvas clipping does not invent an edge.
            mask = ModifySelectionMaskCommand._feather(mask, radius)[
                radius:-radius, radius:-radius
            ].copy()
        if not np.any(mask):
            return None
        return mask, (x0, y0, x1 - x0, y1 - y0)
