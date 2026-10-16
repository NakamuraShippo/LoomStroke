"""Requested outcome of an Image Size operation (CT-13 spec §9)."""

from __future__ import annotations

from dataclasses import dataclass

from .interpolation import InterpolationMode


@dataclass(frozen=True, slots=True)
class ImageSizeTarget:
    """What the user asked for: pixel size, DPI and how to get there.

    ``resample=False`` means "Resample OFF": the pixel size is kept and only
    the DPI (hence the physical size) changes.  The canvas stores a single
    integer DPI, so the target carries one value as well.
    """

    width_px: int
    height_px: int
    dpi: int
    interpolation: InterpolationMode = InterpolationMode.BICUBIC
    resample: bool = True
