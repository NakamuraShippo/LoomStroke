"""Tight ink neighbourhood only while a nominal source and its mask are unchanged."""

import copy
import math
from collections.abc import Mapping

from .membership import PART_KEY


def nominal_edge_metadata(metadata, points, endpoints, mask):
    result = copy.deepcopy(metadata)
    result[PART_KEY]["nominal_ink_region"] = {
        "outline": [list(p) for p in points],
        "endpoints": [list(p) for p in endpoints],
        "mask_key": int(mask.cacheKey()),
    }
    return result


def nominal_edge_region(layer, page_size):
    marker = (getattr(layer, "metadata", None) or {}).get(PART_KEY, {})
    hint = marker.get("nominal_ink_region")
    mask = getattr(layer, "layer_mask", None)
    if (
        not isinstance(hint, Mapping)
        or marker.get("role") != "edge"
        or mask is None
        or not getattr(layer, "mask_enabled", True)
        or getattr(layer, "mask_edit_mode", False)
        or mask.cacheKey() != hint.get("mask_key")
    ):
        return None
    if not _point_list(hint.get("outline"), minimum=3) or not _point_list(
        hint.get("endpoints"), minimum=2, exact=True
    ):
        return None
    objects = getattr(layer, "vector_objects", ())
    if not objects:
        return None
    expected = tuple(tuple(p) for p in hint["outline"])
    for obj in objects:
        if (
            len(obj.paths) != 1
            or not obj.paths[0].closed
            or tuple(a.pos for a in obj.paths[0].anchors) != expected
            or any(a.h_in or a.h_out for a in obj.paths[0].anchors)
            or obj.style.fill_enabled
            or obj.style.dash
            or obj.style.join != "miter"
        ):
            return None
    padding = max(obj.style.stroke_width * max(1, obj.style.miter_limit / 2) + 4 for obj in objects)
    a, b = hint["endpoints"]
    x0, y0 = max(0, math.floor(min(a[0], b[0]) - padding)), max(
        0, math.floor(min(a[1], b[1]) - padding)
    )
    x1, y1 = min(page_size[0], math.ceil(max(a[0], b[0]) + padding)), min(
        page_size[1], math.ceil(max(a[1], b[1]) + padding)
    )
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def _point_list(points, *, minimum, exact=False):
    return (
        isinstance(points, (list, tuple))
        and (len(points) == minimum if exact else len(points) >= minimum)
        and all(
            isinstance(point, (list, tuple))
            and len(point) == 2
            and all(type(value) in {int, float} and math.isfinite(value) for value in point)
            for point in points
        )
    )
