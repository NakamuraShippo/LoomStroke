"""Detached selection snapshots and CPU wand preparation."""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage, QPainter, QTransform

from loomstroke.core.command_layer_access import _layer_transform
from loomstroke.core.commands import ModifySelectionMaskCommand, UpdateSelectionMaskCommand
from loomstroke.core.fill.flood_fill import flood_fill_spans
from loomstroke.core.selection_clipboard import SelectionPayload

from .editing_common import invalid
from .vector_editing import editable_layer


def selection_payload(canvas, layer_id=None, *, editing=True):
    target_id = layer_id or canvas.current_layer().id
    layer = editable_layer(canvas, target_id) if editing else canvas._get_layer_by_id(target_id)
    if layer is None:
        raise invalid("The layer does not exist.", "layerId")
    if layer.layer_type != "raster" or layer.image is None or layer.image.isNull():
        raise invalid("Selection pixels require a raster layer.", "layerId")
    mask, bounds = UpdateSelectionMaskCommand._read_selection(canvas)
    if mask is None or bounds is None:
        raise invalid("A nonempty selection is required.", "selection")
    x, y, width, height = bounds
    # Match the existing selection tool's unmasked, offset-aware payload.
    # Paste/transform commands apply coverage exactly once.
    image = layer.image.copy(x - int(layer.offset_x), y - int(layer.offset_y), width, height)
    alpha = UpdateSelectionMaskCommand._mask_to_numpy(mask, canvas._width, canvas._height)
    patch = alpha[y : y + height, x : x + width]
    return SelectionPayload(
        image,
        UpdateSelectionMaskCommand._numpy_to_qimage(patch, width, height),
        bounds,
        layer.id,
        canvas._dpi,
    )


def reference_array(canvas, layer_id):
    layer = canvas._get_layer_by_id(layer_id)
    if layer is None:
        raise invalid("The reference layer does not exist.", "layerId")
    if layer.image is None or layer.image.isNull():
        raise invalid("Wand reference requires a raster image.", "layerId")
    image = QImage(canvas._width, canvas._height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    try:
        transform = _layer_transform(layer).to_affine()
        painter.setTransform(
            QTransform(
                transform.a, transform.c, transform.b, transform.d, transform.tx, transform.ty
            )
        )
        painter.drawImage(0, 0, layer.image)
    finally:
        painter.end()
    return image_array(image)


def image_array(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(
        image.constBits(), dtype=np.uint8, count=image.bytesPerLine() * image.height()
    ).reshape(image.height(), image.bytesPerLine())
    return rows[:, : image.width() * 4].reshape(image.height(), image.width(), 4).copy()


def wand_mask(reference, x, y, tolerance, contiguous, gap):
    height, width = reference.shape[:2]
    if not 0 <= x < width or not 0 <= y < height:
        raise invalid("Wand seed lies outside the canvas.", "point")
    if not contiguous:
        difference = np.abs(reference.astype(np.int16) - reference[y, x].astype(np.int16)).max(
            axis=2
        )
        return (difference <= tolerance).astype(np.uint8) * 255
    result = flood_fill_spans(reference, x, y, tolerance=tolerance, gap_close_px=gap)
    mask = np.zeros((height, width), dtype=np.uint8)
    for row, left, right in result.spans:
        mask[row, left : right + 1] = 255
    return mask


def modified_mask(alpha, action, radius):
    if action == "invert":
        return 255 - alpha
    method = {
        "expand": ModifySelectionMaskCommand._dilate,
        "contract": ModifySelectionMaskCommand._erode,
        "feather": ModifySelectionMaskCommand._feather,
    }[action]
    return method(alpha, radius)
