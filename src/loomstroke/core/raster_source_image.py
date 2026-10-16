"""Read a placed raster layer as an immutable, clipped document-space image."""

from types import SimpleNamespace
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .command_types import LayerTransform
from .raster_selection_reference import image_rgba_copy, raster_selection_reference


def placed_source_image(
    canvas: Any, layer: Any, image: QImage, width: int, height: int, *, already_placed: bool = False
) -> QImage | None:
    source = layer
    if already_placed:
        # Group/vector rasterizers already produce document-space pixels and
        # apply their own masks. Ancestor masks still constrain the input.
        source = SimpleNamespace(
            transform=LayerTransform.identity(),
            offset_x=0,
            offset_y=0,
            parent_id=getattr(layer, "parent_id", None),
            layer_mask=None,
        )
    projected = raster_selection_reference(canvas, source, image_rgba_copy(image), width, height)
    if projected is None:
        return None
    rgba, coverage = projected
    rgba[:, :, 3] = ((rgba[:, :, 3].astype(np.uint16) * coverage + 127) // 255).astype(np.uint8)
    output = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    return output.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
