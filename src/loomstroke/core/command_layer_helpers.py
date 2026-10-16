"""Layer helper functions used by command implementations."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

logger = logging.getLogger(__name__)


def layer_offset(layer: Any) -> tuple[int, int]:
    try:
        return int(getattr(layer, "offset_x", 0)), int(getattr(layer, "offset_y", 0))
    except Exception as exc:
        logger.debug("layer_offset: failed to read offset attributes: %s", exc)
        return 0, 0


def bump_layer_mask_version(layer: Any) -> None:
    attribute = (
        "filter_mask_version" if hasattr(layer, "filter_mask_version") else "effect_mask_version"
    )
    if not hasattr(layer, attribute):
        return
    try:
        setattr(layer, attribute, int(getattr(layer, attribute, 0)) + 1)
    except Exception as exc:
        logger.debug("bump_layer_mask_version failed: %s", exc)


def mask_size_matches_layer_image(layer: Any, mask: Any) -> bool:
    image = getattr(layer, "image", None)
    if image is None or mask is None:
        return False
    try:
        return (
            not image.isNull()
            and not mask.isNull()
            and int(mask.width()) == int(image.width())
            and int(mask.height()) == int(image.height())
        )
    except Exception as exc:
        logger.debug("mask_size_matches_layer_image failed: %s", exc)
        return False


def layer_mask_to_local(layer: Any, mask: Any, width: int, height: int) -> Any:
    """Return a layer-local mask matching the current image dimensions."""
    if mask_size_matches_layer_image(layer, mask):
        return mask.copy()

    local = QImage(int(width), int(height), mask.format())
    local.fill(Qt.GlobalColor.white)
    offset_x, offset_y = layer_offset(layer)
    painter = QPainter(local)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.drawImage(-offset_x, -offset_y, mask)
    painter.end()
    return local


def shift_layer_mask(layer: Any, dx: int, dy: int) -> None:
    """Shift layer_mask pixels by (dx, dy) so the mask follows the layer."""
    mask = getattr(layer, "layer_mask", None)
    if mask is None:
        return
    if mask_size_matches_layer_image(layer, mask):
        return

    width, height = mask.width(), mask.height()
    shifted = QImage(width, height, mask.format())
    shifted.fill(Qt.GlobalColor.white)
    painter = QPainter(shifted)
    painter.drawImage(dx, dy, mask)
    painter.end()
    layer.layer_mask = shifted
    bump_layer_mask_version(layer)
