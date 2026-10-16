"""Read-only layer content bounds helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.commands import _qimage_to_numpy_view


class CanvasLayerContentBoundsService:
    """Compute layer content bounds without mutating canvas state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def get_layer_content_bounds(
        self, layer_id: str | None = None
    ) -> tuple[int, int, int, int] | None:
        target = self._owner._snapshot_layer(layer_id)
        image: QImage | None
        if target.layer_type == "vector":
            image = self._owner._rasterize_vector_layer(target)
        else:
            image = target.image
        if image is None or image.isNull():
            return None
        if image.format() == QImage.Format.Format_ARGB32_Premultiplied:
            image = image.convertToFormat(QImage.Format.Format_ARGB32)
        view = _qimage_to_numpy_view(image)
        if view is None:
            return None
        # view shape: (H, W, 4) in BGRA order; alpha is channel 3.
        alpha = view[:, :, 3]
        coords = np.argwhere(alpha > 0)
        if coords.size == 0:
            return None
        y_min, x_min = coords.min(axis=0)
        y_max, x_max = coords.max(axis=0)
        ox, oy = self._owner._layer_offset(target)
        return (
            int(x_min) + ox,
            int(y_min) + oy,
            int(x_max - x_min + 1),
            int(y_max - y_min + 1),
        )
