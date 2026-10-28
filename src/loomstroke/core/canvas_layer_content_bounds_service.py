"""Read-only layer content bounds helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.commands import _qimage_to_numpy_view

from .alpha_content_bounds import alpha_content_bounds


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
        bounds = self.bounds_for_alpha(alpha)
        if bounds is None:
            return None
        x_min, y_min, width, height = bounds
        ox, oy = self._owner._layer_offset(target)
        return (
            int(x_min) + ox,
            int(y_min) + oy,
            width,
            height,
        )

    bounds_for_alpha = staticmethod(alpha_content_bounds)
