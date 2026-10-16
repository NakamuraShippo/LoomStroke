"""Prepare metadata and vector copies before mutating a resampled canvas."""

from __future__ import annotations

from typing import Any

from loomstroke.core.canvas_geometry_service import CanvasGeometryService


def prepare_metadata(canvas: Any, vectors: Any, sx: float, sy: float, mode: Any) -> list[Any]:
    prepared = []
    registry = vectors.meta_registry
    for layer in canvas._flatten_layers():
        metadata = registry.resample_meta(getattr(layer, "metadata", None), sx, sy, mode)
        objects = vectors.scale_objects(list(getattr(layer, "vector_objects", None) or []), sx, sy)
        for index, obj in enumerate(objects):
            meta = registry.resample_meta(getattr(obj, "meta", None), sx, sy, mode)
            if meta is not None:
                objects[index] = CanvasGeometryService.rebuild_like(obj, {"meta": meta})
        prepared.append((layer, metadata, objects))
    return prepared
