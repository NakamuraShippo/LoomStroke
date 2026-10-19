"""ICC-aware ink colors in the existing shared color descriptor metadata."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from functools import lru_cache
from hashlib import sha256

import numpy as np

from loomstroke.core.color_state import ColorDescriptor

from .color_transform import CmykColorTransform

_INK_META = "loomstroke.cmyk"


@lru_cache(maxsize=16)
def _profile_id(profile: bytes) -> str:
    return sha256(CmykColorTransform.validate_profile(profile)).hexdigest()


def ink_percentages(
    descriptor: ColorDescriptor, profile: bytes
) -> tuple[float, float, float, float] | None:
    """Return original inks only for their own ICC profile; ignore invalid metadata."""
    payload = (descriptor.meta or {}).get(_INK_META)
    if not isinstance(payload, Mapping) or payload.get("profile_sha256") != _profile_id(profile):
        return None
    values = payload.get("percentages")
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 100
        or not math.isfinite(value)
        for value in values
    ):
        return None
    return tuple(float(value) for value in values)


def without_inks(descriptor: ColorDescriptor) -> ColorDescriptor:
    meta = dict(descriptor.meta or {})
    meta.pop(_INK_META, None)
    return ColorDescriptor(descriptor.rgba, descriptor.blend_mode, meta or None)


@lru_cache(maxsize=512)
def _ink_preview(percentages: tuple, profile: bytes) -> tuple[float, float, float]:
    inks = np.rint(np.asarray(percentages).reshape(1, 1, 4) * 65535 / 100).astype(np.uint16)
    rgb = CmykColorTransform.to_rgb(inks, np.full((1, 1), 65535, np.uint16), profile)[0, 0]
    return tuple(float(value) / 255 for value in rgb[:3])


def from_ink_percentages(
    percentages: Sequence[float], profile: bytes, base: ColorDescriptor | None = None
) -> ColorDescriptor:
    values = tuple(float(value) for value in percentages)
    if len(values) != 4 or any(not math.isfinite(v) or not 0 <= v <= 100 for v in values):
        raise ValueError("CMYK percentages must be between 0 and 100")
    profile_id = _profile_id(profile)
    base = base or ColorDescriptor((0, 0, 0, 1))
    meta = dict(base.meta or {})
    meta[_INK_META] = {"percentages": list(values), "profile_sha256": profile_id}
    return ColorDescriptor((*_ink_preview(values, profile), base.rgba[3]), base.blend_mode, meta)


@lru_cache(maxsize=512)
def _rgb_inks(rgb: tuple, profile: bytes) -> tuple[float, float, float, float]:
    rgba = np.zeros((1, 1, 4), np.uint8)
    rgba[0, 0] = (*np.rint(np.asarray(rgb) * 255).astype(np.uint8), 255)
    inks, _ = CmykColorTransform.from_rgb(rgba, profile)
    return tuple(float(value) * 100 / 255 for value in inks[0, 0])


def for_cmyk_profile(descriptor: ColorDescriptor, profile: bytes | None) -> ColorDescriptor:
    """Project a color without modifying its stored source or round-tripping native inks."""
    if profile is None:
        return descriptor
    percentages = ink_percentages(descriptor, profile)
    if percentages is None:
        percentages = _rgb_inks(descriptor.clamp().rgba[:3], profile)
    return from_ink_percentages(percentages, profile, descriptor)


def set_canvas_foreground(canvas, descriptor: ColorDescriptor) -> None:
    getter = getattr(canvas, "native_cmyk", None)
    if not callable(getter):
        return
    service = getter()
    profile = service.profile()
    service.set_foreground(ink_percentages(descriptor, profile) if profile is not None else None)
