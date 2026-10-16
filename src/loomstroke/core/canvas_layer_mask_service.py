"""Layer-attached mask helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .events import Events
from .mask_coverage import coverage_to_mask_image

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasLayerMaskService:
    """Coordinate layer-attached mask queries and mutations."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def add_layer_mask(self, layer_id: str) -> bool:
        """Add a white, fully visible mask to the specified layer."""
        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None or layer.layer_mask is not None:
            return False
        width, height = self._owner._width, self._owner._height
        raw = bytes(b"\xff" * (width * height * 4))
        mask = QImage(
            raw,
            width,
            height,
            width * 4,
            QImage.Format.Format_ARGB32_Premultiplied,
        ).copy()
        if mask.isNull():
            logger.error("Failed to create mask QImage %dx%d", width, height)
            return False
        layer.layer_mask = mask
        layer.mask_enabled = True
        self._owner._invalidate_layer_cache()
        self._owner._recompose()
        self._owner._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=layer,
            change_type="mask_added",
        )
        return True

    def remove_layer_mask(self, layer_id: str) -> bool:
        """Remove the attached mask from a layer."""
        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None or layer.layer_mask is None:
            return False
        layer.layer_mask = None
        self._owner._invalidate_layer_cache()
        self._owner._recompose()
        self._owner._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=layer,
            change_type="mask_removed",
        )
        return True

    def set_layer_mask_enabled(self, layer_id: str, enabled: bool) -> bool:
        """Toggle the mask on or off without deleting it."""
        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None or layer.layer_mask is None:
            return False
        layer.mask_enabled = enabled
        self._owner._invalidate_layer_cache()
        self._owner._recompose()
        return True

    def get_layer_mask(self, layer_id: str) -> QImage | None:
        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None or layer.layer_mask is None:
            return None
        return cast(QImage, layer.layer_mask.copy())

    def set_mask_edit_mode(self, layer_id: str, enabled: bool) -> bool:
        """Toggle mask editing mode for a layer."""
        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None:
            return False
        if enabled and layer.layer_mask is None:
            return False
        layer.mask_edit_mode = enabled
        if not enabled and self._owner._mask_paint_dirty:
            self._owner._mask_commit_timer.stop()
            self._owner._commit_mask_paint()
        self._owner._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=layer,
            change_type="mask_edit_mode",
        )
        return True

    def selection_to_layer_mask(self, layer_id: str) -> bool:
        """Convert the current selection into a grayscale layer mask."""
        sel_mask = self._owner._selection_mask
        if sel_mask is None:
            return False
        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None:
            return False

        width, height = self._owner._width, self._owner._height
        sel = sel_mask.convertToFormat(QImage.Format.Format_Alpha8)
        sel = sel.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio)
        layer.layer_mask = coverage_to_mask_image(sel)
        layer.mask_enabled = True
        self._owner._invalidate_layer_cache()
        self._owner._recompose()
        self._owner._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=layer,
            change_type="mask_from_selection",
        )
        return True
