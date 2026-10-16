"""Mask layer operations for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasMaskLayerService:
    """Coordinate operations on standalone mask layers."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def invert_mask_layer(self, layer_id: str) -> bool:
        layer = self._owner._get_layer_by_id(layer_id)
        if layer is None or layer.layer_type != "mask" or layer.image is None:
            return False
        img = layer.image
        width, height = img.width(), img.height()
        ptr = img.bits()
        if ptr is None:
            return False
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4)).copy()
        arr[:, :, 3] = 255 - arr[:, :, 3]
        dest = img.bits()
        if dest is None:
            return False
        dest_buf = memoryview(dest)[: arr.nbytes]
        dest_buf[:] = bytes(arr.data)
        layer.texture_version += 1
        self._owner._invalidate_layer_cache()
        self._owner._recompose()
        return True

    def load_mask_as_selection(self, layer_id: str) -> bool:
        layer = self._owner._get_layer_by_id(layer_id)
        if layer is None or layer.layer_type != "mask" or layer.image is None:
            return False
        img = layer.image
        mask = QImage(img.width(), img.height(), QImage.Format.Format_Alpha8)
        mask.fill(0)
        ptr_src = img.bits()
        ptr_dst = mask.bits()
        if ptr_src is None or ptr_dst is None:
            return False
        src_arr = np.frombuffer(ptr_src, dtype=np.uint8).reshape((img.height(), img.width(), 4))
        alpha = src_arr[:, :, 3].copy()
        dst_buf = memoryview(ptr_dst)[: alpha.nbytes]
        dst_buf[:] = bytes(alpha.data)
        self._owner.set_selection_mask(mask, None)
        logger.info("Loaded mask layer %s as selection", layer.name)
        return True
