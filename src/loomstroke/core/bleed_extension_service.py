"""Prepare bleed extension through the existing whole-image replacement Command."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .bleed_geometry import extend_bleed_pixels, trim_bounds
from .command_layer_access import _layer_transform
from .command_types import LayerTransform
from .commands_layer_image_replace import ReplaceLayerImageCommand
from .document_capture_guard import is_document_capture_blocked
from .layer_command_commit_context import _LayerCommandContext


class BleedExtensionService:
    def prepare(
        self, canvas: Any, guides: list[dict], method: str, *, layer_id: str
    ) -> ReplaceLayerImageCommand | None:
        layer = canvas._get_active_layer()
        if layer is None or layer.id != layer_id:
            raise ValueError("Active layer changed")
        scope = _LayerCommandContext(canvas, (layer,))
        channels = canvas.channels()
        if (
            is_document_capture_blocked(canvas)
            or getattr(canvas, "_in_transaction", False)
            or layer.layer_type != "raster"
            or layer.locked
            or getattr(layer, "alpha_lock", False)
            or getattr(layer, "native_cmyk", None) is not None
            or getattr(layer, "clipping", False)
            or (
                getattr(layer, "layer_mask", None) is not None
                and getattr(layer, "mask_enabled", True)
            )
            or getattr(canvas, "_mask_edit_flag", False)
            or channels.enabled(layer)
            or _layer_transform(layer) != LayerTransform.identity()
        ):
            raise ValueError("Unsupported bleed extension target")
        image = layer.image
        size = (canvas._width, canvas._height)
        if (
            not isinstance(image, QImage)
            or (image.width(), image.height()) != size
            or image.format()
            not in (
                QImage.Format.Format_ARGB32,
                QImage.Format.Format_ARGB32_Premultiplied,
                QImage.Format.Format_RGB32,
            )
        ):
            raise ValueError("A full-canvas RGBA raster is required")
        trim = trim_bounds(guides, size)
        if trim is None:
            raise ValueError("Physical trim guides are required")
        array = (
            np.frombuffer(image.constBits(), np.uint8)
            .reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4]
            .reshape(image.height(), image.width(), 4)
        )
        updated = extend_bleed_pixels(array, trim, method)
        if not scope.current():
            raise ValueError("Page or layer changed")
        if updated is None:
            return None
        replacement = QImage(
            updated.data, image.width(), image.height(), updated.strides[0], image.format()
        ).copy()
        return ReplaceLayerImageCommand(layer.id, replacement)
