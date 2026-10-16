"""Mix filtered pixels with brush coverage; never evaluate filters here."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names

from ..geometry import Affine2D
from ..raster_edit_geometry import RasterEditGeometry, sample_mask
from .snapshot import FilterPenSnapshot


def image_array(image: QImage) -> NDArray[Any]:
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    rows = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
        image.height(), image.bytesPerLine()
    )
    return rows[:, : image.width() * 4].reshape(image.height(), image.width(), 4).copy()


def selection_coverage(snapshot: FilterPenSnapshot, region: QRect) -> NDArray[Any] | None:
    if snapshot.geometry is not None or snapshot.clip_masks:
        geometry = snapshot.geometry or RasterEditGeometry(
            Affine2D.translation(*snapshot.origin), snapshot.image.width(), snapshot.image.height()
        )
        x, y = geometry.document_grid(region)
        width, height = snapshot.canvas_size
        result = ((x >= 0) & (y >= 0) & (x < width) & (y < height)).astype(np.float32)
        masks = snapshot.clip_masks + (
            (snapshot.selection,) if snapshot.selection is not None else ()
        )
        for mask in masks:
            result *= sample_mask(mask, x, y, snapshot.canvas_size).astype(np.float32) / 255
        return result
    if snapshot.selection is None:
        return None
    # Selection storage is a canvas-size alpha image, including for bounded selections.
    mask = snapshot.selection
    result = np.zeros((region.height(), region.width()), dtype=np.float32)
    canvas_region = QRect(region)
    canvas_region.translate(*snapshot.origin)
    intersection = canvas_region.intersected(mask.rect())
    if snapshot.selection_bounds is not None:
        intersection = intersection.intersected(snapshot.selection_bounds)
    if intersection.isEmpty():
        return result
    rows = np.frombuffer(mask.constBits(), dtype=np.uint8).reshape(
        mask.height(), mask.bytesPerLine()
    )
    x = intersection.x() - canvas_region.x()
    y = intersection.y() - canvas_region.y()
    result[y : y + intersection.height(), x : x + intersection.width()] = (
        rows[
            intersection.y() : intersection.y() + intersection.height(),
            intersection.x() : intersection.x() + intersection.width(),
        ].astype(np.float32)
        / 255.0
    )
    return result


def mix_patch(
    snapshot: FilterPenSnapshot,
    filtered: QImage,
    region: QRect,
    coverage: NDArray[Any],
    strength: float,
) -> QImage:
    before = image_array(snapshot.image.copy(region))
    after = image_array(filtered.copy(region))
    amount = np.asarray(coverage, dtype=np.float32) / 255.0
    selection = selection_coverage(snapshot, region)
    if selection is not None:
        amount *= selection
    amount *= min(1.0, max(0.0, float(strength)))
    mixed = np.clip(
        before.astype(np.float32) * (1.0 - amount[..., None])
        + after.astype(np.float32) * amount[..., None]
        + 0.5,
        0,
        255,
    ).astype(np.uint8)
    if snapshot.alpha_lock:
        # Match the existing paint contract: preserve alpha and premultiplied validity.
        mixed[:, :, 3] = before[:, :, 3]
        np.minimum(mixed[:, :, :3], mixed[:, :, 3:4], out=mixed[:, :, :3])
    height, width = mixed.shape[:2]
    return QImage(
        mixed.data, width, height, width * 4, QImage.Format.Format_ARGB32_Premultiplied
    ).copy()


_bind_legacy_names(__name__, {"EffectPenSnapshot": "FilterPenSnapshot"})
