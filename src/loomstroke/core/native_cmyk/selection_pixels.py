"""Owned native clipboard samples and integer selection compositing."""

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.geometry import Affine2D
from loomstroke.core.selection_clipboard import SelectionPayload

from .affine import placement, sample
from .color_transform import CmykColorTransform
from .pixels import NativeCmykPixels


@dataclass(slots=True)
class NativeCmykSelectionPayload(SelectionPayload):
    native_cmyk: NativeCmykPixels | None = None


def capture(canvas: Any, layer: Any) -> NativeCmykSelectionPayload | None:
    mask, bounds = canvas._selection_clip_state()
    full_bounds = getattr(canvas, "_selection_bounds_full", None)
    full_mask = getattr(canvas, "_selection_mask_full", None)
    if full_bounds is not None and full_mask is not None:
        from loomstroke.core.commands_selection_mask import (  # noqa: PLC0415
            UpdateSelectionMaskCommand,
        )

        x, y, w, h = full_bounds
        values = UpdateSelectionMaskCommand._mask_to_numpy(full_mask, w, h)
        if values.shape == (h, w) and np.any(values):
            mask = UpdateSelectionMaskCommand._numpy_to_qimage(values, w, h)
            native = sample(layer.native_cmyk, QRect(x, y, w, h), placement(layer))
            return NativeCmykSelectionPayload(
                ChannelPixelPolicy.image(native.preview(), straight=True),
                mask,
                (x, y, w, h),
                layer.id,
                getattr(canvas, "_dpi", None),
                native,
            )
    if mask is None or bounds is None or bounds.isEmpty():
        return None
    native = sample(layer.native_cmyk, bounds, placement(layer))
    return NativeCmykSelectionPayload(
        ChannelPixelPolicy.image(native.preview(), straight=True),
        mask.copy(bounds),
        bounds.getRect(),
        layer.id,
        getattr(canvas, "_dpi", None),
        native,
    )


def compose(first: Affine2D, second: Affine2D) -> Affine2D:
    """Apply second, then first, preserving the existing affine convention."""
    return Affine2D(
        first.a * second.a + first.b * second.c,
        first.a * second.b + first.b * second.d,
        first.c * second.a + first.d * second.c,
        first.c * second.b + first.d * second.d,
        first.a * second.tx + first.b * second.ty + first.tx,
        first.c * second.tx + first.d * second.ty + first.ty,
    )


def mask_pixels(
    payload: SelectionPayload, *, binary: bool = False
) -> tuple[NDArray[Any], NativeCmykPixels | None]:
    from loomstroke.core.commands_selection_content import (  # noqa: PLC0415
        _selection_payload_mask_numpy,
    )

    mask = _selection_payload_mask_numpy(payload)
    if mask is None:
        raise ValueError("A native selection requires a mask")
    if binary:
        mask = np.where(mask > 127, 255, 0).astype(np.uint8)
    profile = getattr(payload, "native_cmyk", None)
    return mask, profile


def payload_pixels(
    payload: SelectionPayload, target: Any, *, binary: bool = False
) -> NativeCmykPixels:
    mask, native = mask_pixels(payload, binary=binary)
    maximum = np.iinfo(target.inks.dtype).max
    if native is None or native.profile != target.profile:
        rgba = (
            ChannelPixelPolicy.pixels(payload.image, straight=True)
            if hasattr(payload.image, "isNull")
            else payload.image
        )
        inks, alpha = CmykColorTransform.from_rgb(rgba, target.profile)
        native = NativeCmykPixels(inks, alpha, target.profile)
    old_maximum = np.iinfo(native.inks.dtype).max
    inks = ((native.inks.astype(np.uint64) * maximum + old_maximum // 2) // old_maximum).astype(
        target.inks.dtype
    )
    alpha = (native.alpha.astype(np.uint64) * mask * maximum + old_maximum * 127) // (
        old_maximum * 255
    )
    return NativeCmykPixels(inks, alpha.astype(target.inks.dtype), target.profile)


def over(
    before: NativeCmykPixels,
    source: NativeCmykPixels,
    selected: tuple[int, ...],
    *,
    alpha_lock: bool = False,
) -> NativeCmykPixels:
    maximum = np.iinfo(before.inks.dtype).max
    opacity = source.alpha.astype(np.uint64)
    alpha = before.alpha.astype(np.uint64)
    inks = before.inks.astype(np.uint64)
    remaining = maximum - opacity
    if tuple(selected) != (0, 1, 2, 3) or alpha_lock:
        for channel in selected:
            inks[:, :, channel] = (
                inks[:, :, channel] * remaining
                + source.inks[:, :, channel].astype(np.uint64) * opacity
                + maximum // 2
            ) // maximum
    else:
        denominator = opacity * maximum + alpha * remaining
        for channel in selected:
            numerator = (
                source.inks[:, :, channel].astype(np.uint64) * opacity * maximum
                + inks[:, :, channel] * alpha * remaining
            )
            mixed = (numerator + denominator // 2) // np.maximum(denominator, 1)
            inks[:, :, channel] = np.where(opacity > 0, mixed, inks[:, :, channel])
        alpha = (denominator + maximum // 2) // maximum
    return NativeCmykPixels(
        inks.astype(before.inks.dtype), alpha.astype(before.alpha.dtype), before.profile
    )


def transformed_mask(
    payload: SelectionPayload, transform: Affine2D, width: int, height: int, *, binary: bool = False
) -> QImage:
    mask, _ = mask_pixels(payload, binary=binary)
    from PySide6.QtGui import QPainter, QTransform  # noqa: PLC0415

    from loomstroke.core.commands_selection_mask import (  # noqa: PLC0415
        UpdateSelectionMaskCommand,
    )

    source = UpdateSelectionMaskCommand._numpy_to_qimage(mask, mask.shape[1], mask.shape[0])
    result = QImage(width, height, QImage.Format.Format_Alpha8)
    result.fill(0)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not binary)
    painter.setTransform(
        QTransform(transform.a, transform.c, transform.b, transform.d, transform.tx, transform.ty)
    )
    painter.drawImage(0, 0, source)
    painter.end()
    return result
