"""Detect vector content outside the GUI SVG export subset."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..core.geometry import Affine2D


def select_svg_layers(
    layers: Sequence[Any], legacy_issue: Callable[[Any], str | None]
) -> tuple[list[tuple[list[Any], float]], int, int]:
    groups = []
    omitted_legacy = 0
    omitted_layout = _count_grouped_vectors(layers)
    for layer in layers:
        if getattr(layer, "layer_type", "raster") != "vector" or not getattr(
            layer, "visible", True
        ):
            continue
        if legacy_issue(layer) is not None:
            omitted_legacy += 1
            continue
        objects = list(getattr(layer, "vector_objects", []) or [])
        if not objects:
            continue
        if _has_unsupported_layout(layer):
            omitted_layout += 1
        else:
            groups.append((objects, getattr(layer, "opacity", 1.0)))
    return groups, omitted_legacy, omitted_layout


def _count_grouped_vectors(layers: Sequence[Any]) -> int:
    pending = [
        child
        for layer in layers
        if getattr(layer, "visible", True)
        for child in getattr(layer, "children", []) or []
    ]
    count = 0
    while pending:
        layer = pending.pop()
        if not getattr(layer, "visible", True):
            continue
        if getattr(layer, "layer_type", "") == "vector" and getattr(layer, "vector_objects", None):
            count += 1
        pending.extend(getattr(layer, "children", []) or [])
    return count


def _has_unsupported_layout(layer: Any) -> bool:
    if getattr(layer, "layer_mask", None) is not None and getattr(layer, "mask_enabled", True):
        return True
    if getattr(layer, "offset_x", 0) or getattr(layer, "offset_y", 0):
        return True
    transform = getattr(layer, "transform", None)
    if transform is None:
        return False
    to_affine = getattr(transform, "to_affine", None)
    return not callable(to_affine) or to_affine() != Affine2D.identity()
