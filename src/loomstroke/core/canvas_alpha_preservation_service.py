"""Alpha preservation helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter


class CanvasAlphaPreservationService:
    """Save and restore layer alpha patches for alpha-lock operations."""

    def __init__(self, owner: Any | None = None) -> None:
        self._owner = owner

    def save_alpha(self, layer: Any, local_bounds: QRect) -> NDArray[Any] | None:
        """Save alpha channel of *local_bounds* if alpha lock is active."""
        if not getattr(layer, "alpha_lock", False) or layer.image is None:
            return None
        patch = layer.image.copy(local_bounds)
        patch = patch.convertToFormat(QImage.Format.Format_ARGB32)
        h, w = patch.height(), patch.width()
        if h == 0 or w == 0:
            return None
        ptr = patch.constBits()
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, w, 4).copy()  # BGRA
        return arr[:, :, 3]  # alpha channel only

    def save_flood_fill_alpha(
        self,
        reference_layer_id: str | None,
    ) -> tuple[Any | None, NDArray[Any] | None]:
        """Resolve and save the layer alpha used by flood-fill submit."""

        owner = self._owner
        if owner is None:
            return None, None
        layer = (
            owner._get_layer_by_id(reference_layer_id)
            if reference_layer_id
            else owner._get_active_layer()
        )
        if layer is None or layer.image is None:
            return layer, None
        bounds = QRect(0, 0, layer.image.width(), layer.image.height())
        return layer, self.save_alpha(layer, bounds)

    def save_gradient_alpha(self, cmd: Any) -> tuple[Any | None, NDArray[Any] | None]:
        """Resolve and save the layer alpha used by gradient submit."""

        owner = self._owner
        if owner is None:
            return None, None
        layer_id = getattr(cmd, "_layer_id", None) or getattr(cmd, "layer_id", None)
        layer = owner._get_layer_by_id(layer_id) if layer_id else owner._get_active_layer()
        if layer is None or not getattr(layer, "alpha_lock", False) or layer.image is None:
            return layer, None

        backup = getattr(cmd, "_backup", None)
        if backup is not None:
            return layer, backup[:, :, 3].copy()

        bounds = QRect(0, 0, layer.image.width(), layer.image.height())
        return layer, self.save_alpha(layer, bounds)

    @staticmethod
    def restore_alpha(layer: Any, local_bounds: QRect, saved: NDArray[Any] | None) -> None:
        """Restore saved alpha channel over *local_bounds* of layer image.

        Works on premultiplied ARGB32 images: after replacing the alpha
        channel, RGB components are clamped so they never exceed alpha
        (premultiplication invariant).
        """
        if saved is None or layer.image is None:
            return
        img = layer.image
        x0, y0 = local_bounds.x(), local_bounds.y()
        sh, sw = saved.shape
        ih, iw = img.height(), img.width()
        # Clamp patch to image bounds
        rw = min(sw, iw - x0)
        rh = min(sh, ih - y0)
        if rw <= 0 or rh <= 0 or x0 < 0 or y0 < 0:
            return
        # Extract the patch region from the full image via copy + write-back
        patch = img.copy(QRect(x0, y0, rw, rh))
        patch = patch.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        ptr = patch.bits()
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(rh, rw, 4)  # BGRA
        alpha_slice = saved[:rh, :rw]
        # Restore alpha and clamp RGB <= alpha (premultiplication invariant)
        arr[:, :, 3] = alpha_slice
        a = alpha_slice[:, :, np.newaxis]  # broadcast
        np.minimum(arr[:, :, :3], a, out=arr[:, :, :3])
        # Write patch back
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawImage(x0, y0, patch)
        p.end()

    def restore_layer_alpha(self, layer: Any | None, saved: NDArray[Any] | None) -> None:
        """Restore saved alpha over the whole layer image."""

        if saved is None or layer is None or layer.image is None:
            return
        bounds = QRect(0, 0, layer.image.width(), layer.image.height())
        self.restore_alpha(layer, bounds, saved)
