"""Qt image composition for a four-corner raster selection."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage, QPainter

from .command_image_utils import _qimage_from_bgra
from .commands_selection_mask import UpdateSelectionMaskCommand
from .selection_clipboard import SelectionPayload
from .selection_projective import _mask_array


def composite_projective_qimage(
    image: QImage,
    payload: SelectionPayload,
    dest_image: NDArray[np.uint8],
    dest_mask: NDArray[np.uint8],
    dest_bounds: tuple[int, int, int, int],
    *,
    offset: tuple[int, int],
    binary: bool,
    duplicate: bool,
    additive: bool = False,
) -> bool:
    """Apply mask coverage with Qt's image compositor instead of full-frame float arrays."""
    if (
        not binary
        and isinstance(payload.mask, QImage)
        and payload.mask.format() == QImage.Format.Format_Alpha8
    ):
        source_mask_qt = payload.mask
    else:
        source_mask = _mask_array(payload)
        if source_mask is None:
            return False
        if binary:
            source_mask = np.where(source_mask > 127, 255, 0).astype(np.uint8)
        source_mask_qt = UpdateSelectionMaskCommand._numpy_to_qimage(
            source_mask, source_mask.shape[1], source_mask.shape[0]
        )
    masked_image = masked_projective_image(dest_image, dest_mask)
    if source_mask_qt is None or masked_image is None:
        return False

    painter = QPainter(image)
    if not painter.isActive():
        return False
    offset_x, offset_y = offset
    if not duplicate:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        painter.drawImage(
            payload.bounds[0] - offset_x, payload.bounds[1] - offset_y, source_mask_qt
        )
    painter.setCompositionMode(
        QPainter.CompositionMode.CompositionMode_Plus
        if additive
        else QPainter.CompositionMode.CompositionMode_SourceOver
    )
    painter.drawImage(dest_bounds[0] - offset_x, dest_bounds[1] - offset_y, masked_image)
    painter.end()
    return True


def masked_projective_image(
    dest_image: NDArray[np.uint8], dest_mask: NDArray[np.uint8]
) -> QImage | None:
    mask = UpdateSelectionMaskCommand._numpy_to_qimage(
        dest_mask, dest_mask.shape[1], dest_mask.shape[0]
    )
    image = _qimage_from_bgra(dest_image, premultiplied=True)
    if mask is None or image is None:
        return None
    painter = QPainter(image)
    if not painter.isActive():
        return None
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, mask)
    painter.end()
    return image


def clear_qimage_with_mask(image: QImage, mask: QImage, x: int, y: int) -> bool:
    """Clear a temporary selection cut without converting the entire patch to floats."""
    painter = QPainter(image)
    if not painter.isActive():
        return False
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
    painter.drawImage(x, y, mask)
    painter.end()
    return True
