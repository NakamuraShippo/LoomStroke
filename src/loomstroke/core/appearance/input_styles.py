"""Bake a layer's own styles into an isolated input image copy."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .layer_style_rasterizer import LayerStyleRasterizer
from .live_styles import layer_styles


def render_input_styles(image: QImage, layer: Any) -> QImage:
    """Keep image bounds and exclude layer opacity, blend, masks and backdrop."""
    stack = layer_styles(layer)
    if not stack.enabled():
        return image
    source = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = source.width(), source.height()
    stride = source.bytesPerLine()
    pixels = np.frombuffer(source.constBits(), dtype=np.uint8, count=stride * height)
    rgba = pixels.reshape(height, stride)[:, : width * 4].reshape(height, width, 4)
    rendered = np.ascontiguousarray(LayerStyleRasterizer().render(rgba, stack))
    return QImage(rendered.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
