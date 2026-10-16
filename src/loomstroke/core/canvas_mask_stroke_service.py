"""Mask stroke preview helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.mask_coverage import mask_coverage_alpha8, normalize_mask_image

# Backwards-compatible alias: the coverage helpers live in the generic
# ``loomstroke.core.mask_coverage`` module.
layer_mask_to_alpha8 = mask_coverage_alpha8


class CanvasMaskStrokeService:
    """Coordinate mask-edit swap and runtime mask stroke preview drawing."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    @contextlib.contextmanager
    def mask_edit_swap(self, layer: Any | None) -> Iterator[None]:
        """Temporarily swap image and layer_mask when mask edit mode is active."""

        if layer is not None and layer.mask_edit_mode and layer.layer_mask is not None:
            saved_tex_ver = layer.texture_version
            layer.image, layer.layer_mask = layer.layer_mask, layer.image
            try:
                yield
            finally:
                layer.image, layer.layer_mask = layer.layer_mask, layer.image
                layer.texture_version = saved_tex_ver
                self.normalize_mask_to_grayscale(layer.layer_mask)
        else:
            yield

    @staticmethod
    def normalize_mask_to_grayscale(mask: QImage) -> None:
        """Normalize an edited mask to the canonical coverage form in place.

        Brush strokes lower luminance and eraser strokes lower alpha; both
        edits are folded into coverage (RGB = coverage, A = 255) so the stored
        mask never relies on premultiplied-alpha side effects.
        """

        normalize_mask_image(mask)

    def draw_mask_stroke_preview(self, dirty: QRect, layer: Any) -> None:
        """Draw semi-transparent red overlay for a dirty mask stroke region."""

        owner = self._owner
        if owner._composited is None or layer.layer_mask is None:
            return
        canvas_rect = QRect(0, 0, owner._width, owner._height)
        region = dirty.intersected(canvas_rect)
        if region.isEmpty():
            return
        x, y, rw, rh = region.x(), region.y(), region.width(), region.height()

        fmt = QImage.Format.Format_ARGB32_Premultiplied
        mask_region = layer.layer_mask.copy(region).convertToFormat(fmt)
        overlay = QImage(rw, rh, fmt)
        overlay.fill(Qt.GlobalColor.transparent)

        mask_bits = mask_region.bits()
        mask_arr = np.frombuffer(mask_bits, dtype=np.uint8).reshape(rh, rw, 4)
        ov_bits = overlay.bits()
        ov_arr = np.frombuffer(ov_bits, dtype=np.uint8).reshape(rh, rw, 4)

        inv_lum = 255 - mask_arr[:, :, 1]
        alpha = (inv_lum.astype(np.float32) * 0.5).astype(np.uint8)
        ov_arr[:, :, 0] = 0
        ov_arr[:, :, 1] = 0
        ov_arr[:, :, 2] = alpha
        ov_arr[:, :, 3] = alpha

        painter = QPainter(owner._composited)
        painter.drawImage(x, y, overlay)
        painter.end()
        owner.composited_changed.emit(owner._composited)
