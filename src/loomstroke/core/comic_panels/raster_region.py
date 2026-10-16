"""Identify comic primitives eligible for existing tight vector rasterization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .membership import BALLOON_KEY, PART_KEY, TEXT_KEY


def uses_comic_raster_region(layer: Any) -> bool:
    metadata = getattr(layer, "metadata", None)
    if isinstance(metadata, Mapping) and PART_KEY in metadata:
        return True
    for obj in getattr(layer, "vector_objects", ()) or ():
        meta = getattr(obj, "meta", None)
        if isinstance(meta, Mapping) and (BALLOON_KEY in meta or TEXT_KEY in meta):
            return True
    return False


def is_comic_record_carrier(layer: Any) -> bool:
    metadata = getattr(layer, "metadata", None)
    if not isinstance(metadata, Mapping):
        return False
    part = metadata.get(PART_KEY)
    return (
        isinstance(part, Mapping)
        and type(part.get("version")) is int
        and part.get("version") == 1
        and part.get("role") == "carrier"
        and all(not getattr(obj, "paths", ()) for obj in getattr(layer, "vector_objects", ()) or ())
    )
