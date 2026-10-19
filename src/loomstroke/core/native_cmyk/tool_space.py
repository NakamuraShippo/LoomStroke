"""Map document-space brush coverage to transformed native source pixels."""

from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect

from loomstroke.core.geometry import Affine2D
from loomstroke.core.raster_edit_geometry import RasterEditGeometry, sample_mask

from .affine import document_rect, placement, sample, source_rect
from .pixels import NativeCmykPixels
from .selection_pixels import compose


def is_placed(layer: Any) -> bool:
    transform = getattr(layer, "transform", None)
    return transform is not None and (
        transform.scale_x != 1
        or transform.scale_y != 1
        or transform.rotation != 0
        or transform.tx != round(transform.tx)
        or transform.ty != round(transform.ty)
    )


def document_bounds(layer: Any) -> QRect:
    bounds = QRect(0, 0, layer.image.width(), layer.image.height())
    return document_rect(bounds, placement(layer))


def validate_coverage_size(bounds: QRect) -> None:
    if bounds.width() * bounds.height() * 4 > 128 * 1024 * 1024:
        raise ValueError("Native stroke coverage exceeds 128 MiB")


def stroke_region(layer: Any, bounds: QRect) -> QRect:
    """Replay/preimage regions retain their source grid and legacy offset origin."""
    return source_rect(layer.native_cmyk, bounds, placement(layer)).translated(
        layer.offset_x, layer.offset_y
    )


def local_paint(
    layer: Any, bounds: QRect, color: Any, coverage: NDArray[Any]
) -> tuple[QRect, Any, NDArray[Any]]:
    if not is_placed(layer):
        return bounds.translated(-layer.offset_x, -layer.offset_y), color, coverage
    native = layer.native_cmyk
    rect = source_rect(native, bounds, placement(layer))
    if rect.isEmpty():
        return rect, color, np.zeros((0, 0), np.uint8)
    inverse = placement(layer).inverse()
    assert inverse is not None
    transform = compose(inverse, Affine2D.translation(bounds.x(), bounds.y()))
    inks = np.broadcast_to(color, (*coverage.shape, 4)).astype(native.inks.dtype)
    scale = 257 if native.inks.dtype == np.uint16 else 1
    pixels = NativeCmykPixels(inks, coverage.astype(native.inks.dtype) * scale, native.profile)
    projected = sample(pixels, rect, transform)
    coverage = ((projected.alpha.astype(np.uint32) + scale // 2) // scale).astype(np.uint8)
    return rect, projected.inks, coverage


def selection_local(canvas: Any, layer: Any, rect: QRect, coverage: NDArray[Any]) -> NDArray[Any]:
    selection, _ = canvas._selection_clip_state()
    if selection is None:
        return coverage
    if is_placed(layer):
        x, y = RasterEditGeometry.capture(layer).document_grid(rect)
        mask = sample_mask(selection, x, y, (canvas._width, canvas._height))
    else:
        image = selection.copy(rect.translated(layer.offset_x, layer.offset_y)).convertToFormat(
            selection.Format.Format_Alpha8
        )
        rows = np.frombuffer(image.constBits(), np.uint8).reshape(
            image.height(), image.bytesPerLine()
        )
        mask = rows[:, : image.width()]
    return ((coverage.astype(np.uint16) * mask.astype(np.uint16) + 127) // 255).astype(np.uint8)


def clip_replay_region(canvas: Any, layer: Any, rect: QRect) -> QRect | None:
    if (
        getattr(layer, "native_cmyk", None) is not None
        and is_placed(layer)
        and not layer.mask_edit_mode
    ):
        _mask, bounds = canvas._selection_clip_state()
        clipped = rect if bounds is None else rect.intersected(stroke_region(layer, bounds))
        return None if clipped.isEmpty() else clipped
    fallback: QRect | None = canvas._apply_selection_clip(rect)
    return fallback
