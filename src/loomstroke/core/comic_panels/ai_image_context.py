"""Panel-local AI inputs, geometry identity and fitted generated outputs."""

import hashlib
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from ..command_layer_access import _layer_transform, _set_layer_transform
from ..command_types import LayerTransform
from ..raster_source_image import placed_source_image
from .image_placement import PLACEMENT_KEY, fit_image
from .tool_context import panel_tool_context


def panel_input_image(canvas: Any, layer: Any, image: QImage) -> QImage | None:
    context = panel_tool_context(canvas, layer)
    if context is None:
        return image
    width, height = canvas.get_canvas_size()
    projected = placed_source_image(
        canvas, layer, image, width, height, already_placed=layer.layer_type in {"group", "vector"}
    )
    if projected is None:
        return None
    crop = QRect(*context.rect).intersected(QRect(0, 0, width, height))
    return None if crop.isEmpty() else projected.copy(crop)


def panel_input_version(
    canvas: Any, layer: Any, texture_version: int, encoded_input: bytes | None = None
) -> int:
    context = panel_tool_context(canvas, layer)
    if context is None:
        return texture_version
    # Include the actual flattened input: this covers child pixels, transforms
    # and clip edits without using process-local QImage identities in cache keys.
    values = [
        texture_version,
        context.rect,
        hashlib.sha256(encoded_input).hexdigest() if encoded_input is not None else None,
    ]
    return int.from_bytes(hashlib.sha256(repr(values).encode()).digest()[:8], "big")


def place_panel_output(canvas: Any, layer: Any, previous_image: QImage | None = None) -> bool:
    context = panel_tool_context(canvas, layer)
    if context is None or layer.image is None:
        return False
    image = layer.image
    if previous_image is not None and not previous_image.isNull():
        old = _layer_transform(layer)
        sx, sy = previous_image.width() / image.width(), previous_image.height() / image.height()
        ox, oy = old.origin_x / sx, old.origin_y / sy
        transform = LayerTransform(
            tx=old.tx + old.origin_x - ox,
            ty=old.ty + old.origin_y - oy,
            scale_x=old.scale_x * sx,
            scale_y=old.scale_y * sy,
            rotation=old.rotation,
            origin_x=ox,
            origin_y=oy,
        )
    else:
        transform = fit_image((image.width(), image.height()), context.rect).transform
    _set_layer_transform(layer, transform)
    layer.metadata = {
        **(layer.metadata or {}),
        PLACEMENT_KEY: {
            "version": 1,
            "fit_policy": "cover",
            "source_size": [image.width(), image.height()],
        },
    }
    return True
