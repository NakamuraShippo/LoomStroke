"""Convert a fill gesture into a bounded document-coordinate coverage mask."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PySide6.QtGui import QPainterPath, QPainterPathStroker

from loomstroke.core.selection_shapes.selection_shape_mask import SelectionShapeMask
from loomstroke.core.selection_shapes.selection_shape_path import SelectionShapePath


class FillGestureMask:
    @staticmethod
    def path(mode: str, points: Sequence[tuple[float, float]], size: float) -> QPainterPath:
        if mode == "Enclose and Fill":
            return SelectionShapePath.build("Lasso", points)
        path = QPainterPath()
        if not points:
            return path
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        stroker = QPainterPathStroker()
        stroker.setWidth(size)
        outline = stroker.createStroke(path)
        # A single press should also fill a small enclosed gap.
        outline.addEllipse(points[0][0] - size / 2, points[0][1] - size / 2, size, size)
        return outline.simplified()

    @classmethod
    def render(
        cls,
        mode: str,
        points: Sequence[tuple[float, float]],
        size: float,
        canvas_size: tuple[int, int],
    ) -> np.ndarray | None:
        rendered = SelectionShapeMask.render(
            cls.path(mode, points, size), canvas_size, pixel_perfect=True
        )
        if rendered is None:
            return None
        mask, (x, y, width, height) = rendered
        result = np.zeros((canvas_size[1], canvas_size[0]), dtype=np.uint8)
        result[y : y + height, x : x + width] = mask
        result.setflags(write=False)
        return result
