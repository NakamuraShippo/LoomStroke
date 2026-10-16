"""Interpolation mode catalogue for Image Size resampling (CT-13).

The enum values are the *stable ids* passed through the ``resample_canvas``
facade and stored in presets; display names live in the UI layer so that
runtime language switches never touch the stored value.
"""

from __future__ import annotations

from enum import Enum


class InterpolationMode(str, Enum):
    """Resampling kernels offered by Image Size."""

    NEAREST = "nearest"
    BILINEAR = "bilinear"
    BICUBIC = "bicubic"
    LANCZOS = "lanczos"
    HAMMING = "hamming"
    BOX = "box"

    @classmethod
    def default(cls) -> InterpolationMode:
        return cls.BICUBIC

    @classmethod
    def normalize(cls, value: object) -> InterpolationMode | None:
        """Return the mode for *value* (enum member or stable id), ``None`` if unknown.

        Unknown ids are *not* mapped to a fallback kernel: the caller must
        decide (spec §15 forbids a silent nearest fallback).
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            key = value.strip().lower()
            for mode in cls:
                if mode.value == key:
                    return mode
        return None

    @classmethod
    def ids(cls) -> tuple[str, ...]:
        return tuple(mode.value for mode in cls)
