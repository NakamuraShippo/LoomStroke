"""Optional native paint samples in existing editable-object metadata."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from typing import Any, cast

import numpy as np

from loomstroke.core.color_state import RGBA, ColorDescriptor

from .color_descriptor import ink_percentages
from .color_transform import CmykColorTransform

PAINTS_KEY = "loomstroke.cmyk.paints"


def capture_paint(color: ColorDescriptor | None) -> dict[str, Any] | None:
    if not isinstance(color, ColorDescriptor) or not isinstance(color.meta, Mapping):
        return None
    inks = color.meta.get("loomstroke.cmyk")
    if not isinstance(inks, Mapping):
        return None
    return {
        "rgba": [round(v * 255) for v in color.clamp().rgba],
        "loomstroke.cmyk": deepcopy(dict(inks)),
    }


def capture_vector_paints(
    meta: dict[str, Any], color: ColorDescriptor | None, style: Any
) -> dict[str, Any]:
    paint = capture_paint(color)
    if paint is None:
        return meta
    values: dict[str, Any] = {"version": 1}
    for role in ("stroke", "fill"):
        rgb = getattr(style, f"{role}_color", None)
        if rgb is not None and tuple(rgb[:3]) == tuple(paint["rgba"][:3]):
            values[role] = deepcopy(paint)
    return {**meta, PAINTS_KEY: values}


def object_paints(obj: Any) -> Mapping[str, Any]:
    raw = (getattr(obj, "meta", None) or {}).get(PAINTS_KEY)
    return (
        raw
        if (
            isinstance(raw, Mapping)
            and raw.get("version") == 1
            and any(isinstance(raw.get(role), Mapping) for role in ("stroke", "fill", "text"))
        )
        else {}
    )


def without_paint(meta: Mapping[str, Any], role: str) -> dict[str, Any]:
    result = dict(meta)
    raw = result.get(PAINTS_KEY)
    if isinstance(raw, Mapping):
        paints = {key: value for key, value in raw.items() if key != role}
        result[PAINTS_KEY] = paints
    return result


@lru_cache(maxsize=512)
def _rgb_inks(rgba: tuple[int, ...], profile: bytes) -> tuple[int, ...]:
    values = np.asarray(rgba, np.uint8).reshape(1, 1, 4)
    inks, _alpha = CmykColorTransform.from_rgb(values, profile)
    return tuple(int(v) for v in inks[0, 0])


def paint_inks(
    raw: Any, rgba: tuple[int, ...], profile: bytes, dtype: np.dtype[Any]
) -> tuple[int, ...]:
    """Use exact samples only for matching ICC and unchanged display colour."""
    percentages = None
    if isinstance(raw, Mapping) and raw.get("rgba") == list(rgba):
        color = ColorDescriptor(cast("RGBA", tuple(v / 255 for v in rgba)), meta=raw)
        percentages = ink_percentages(color, profile)
    maximum = np.iinfo(dtype).max
    if percentages is not None:
        return tuple(round(v * maximum / 100) for v in percentages)
    scale = 257 if dtype == np.uint16 else 1
    return tuple(v * scale for v in _rgb_inks(rgba, profile))
