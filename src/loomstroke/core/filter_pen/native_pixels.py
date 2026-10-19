"""ICC filter results mixed into native ink tiles; untouched samples never re-separate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.native_cmyk.color_transform import CmykColorTransform
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .pixels import selection_coverage

if TYPE_CHECKING:
    from .snapshot import FilterPenSnapshot


def crop_native(pixels: NativeCmykPixels, rect: QRect) -> NativeCmykPixels:
    x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
    return NativeCmykPixels(
        pixels.inks[y : y + height, x : x + width],
        pixels.alpha[y : y + height, x : x + width],
        pixels.profile,
    )


def mix_native_patch(
    snapshot: FilterPenSnapshot,
    filtered: QImage,
    rect: QRect,
    coverage: NDArray[Any],
    strength: float,
) -> NativeCmykPixels:
    before = crop_native(snapshot.native, rect)
    rgba = ChannelPixelPolicy.pixels(filtered.copy(rect), straight=True)
    source = ChannelPixelPolicy.pixels(snapshot.image.copy(rect), straight=True)
    amount = np.asarray(coverage, np.float64) / 255
    selection = selection_coverage(snapshot, rect)
    if selection is not None:
        amount *= selection
    amount *= min(1.0, max(0.0, float(strength)))
    # An identity filter must preserve K-only inks and all 16-bit low bits.
    amount[np.all(rgba == source, axis=2)] = 0
    active = amount > 0
    if not np.any(active):
        return before
    # The filter still evaluates its full carrier (including kernel halos).
    # Only separate pixels that actually contribute to this ink patch.
    samples = rgba[active]
    inks, alpha = CmykColorTransform.from_rgb(samples[np.newaxis, :, :], before.profile)
    inks, alpha = inks[0], alpha[0]
    if before.bits == 16:
        inks, alpha = inks.astype(np.uint16) * 257, alpha.astype(np.uint16) * 257
    alpha = np.where(samples[:, 3] == source[active, 3], before.alpha[active], alpha)
    amount = amount[active]
    maximum = np.iinfo(inks.dtype).max
    original_alpha = before.alpha[active].astype(np.float64)
    filtered_alpha = alpha.astype(np.float64)
    locked = snapshot.alpha_lock or snapshot.selected_channels != (0, 1, 2, 3)
    out_alpha = (
        original_alpha if locked else original_alpha * (1 - amount) + filtered_alpha * amount
    )
    weight = (
        amount
        if locked
        else np.divide(
            filtered_alpha * amount,
            np.maximum(out_alpha, 1),
        )
    )
    mixed = before.inks.copy()
    for channel in snapshot.selected_channels:
        mixed[active, channel] = np.rint(
            np.clip(
                before.inks[active, channel] * (1 - weight) + inks[:, channel] * weight,
                0,
                maximum,
            )
        ).astype(inks.dtype)
    coverage_alpha = before.alpha.copy()
    coverage_alpha[active] = np.rint(out_alpha).astype(inks.dtype)
    return NativeCmykPixels(mixed, coverage_alpha, before.profile)


def same_native(first: NativeCmykPixels, second: NativeCmykPixels) -> bool:
    return np.array_equal(first.inks, second.inks) and np.array_equal(first.alpha, second.alpha)


def write_native_tiles(layer: Any, tiles: dict[tuple[int, int], Any], *, before: bool) -> None:
    prepared = []
    for tile in tiles.values():
        pixels = tile.before if before else tile.after
        # All fallible ICC conversions precede any live mutation.
        image = ChannelPixelPolicy.image(pixels.preview(), straight=True)
        prepared.append((tile.rect, pixels, image))
    painter = QPainter(layer.image)
    if not painter.isActive():
        raise ValueError("Could not update the native filter preview")
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for rect, pixels, image in prepared:
            layer.native_cmyk.patch(rect.getRect(), pixels)
            painter.drawImage(rect.x(), rect.y(), image)
    finally:
        painter.end()
