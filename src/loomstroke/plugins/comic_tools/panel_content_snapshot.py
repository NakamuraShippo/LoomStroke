"""Allocation units and conservative page bounds captured without rasterization."""

from __future__ import annotations

import copy
from dataclasses import dataclass

from loomstroke.core.comic_panels.image_placement import PLACEMENT_KEY
from loomstroke.core.comic_panels.membership import FRONT_KEY, PANEL_KEY, PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import ContentBounds


@dataclass(frozen=True)
class PanelContentUnit:
    layer_id: str
    parent_id: str
    bounds: ContentBounds
    name: str
    texture_version: int


def _bounds(layer):
    metadata = getattr(layer, "metadata", None) or {}
    placement = metadata.get(PLACEMENT_KEY)
    if placement and layer.image is not None:
        t = layer.transform.to_affine()
        w, h = layer.image.width(), layer.image.height()
        points = [t.apply(*p) for p in ((0, 0), (w, 0), (w, h), (0, h))]
    elif layer.layer_type == "vector":
        points = [a.pos for obj in layer.vector_objects for path in obj.paths for a in path.anchors]
    elif layer.layer_type == "group":
        rects = [_bounds(c) for c in layer.children]
        if not rects or any(r is None for r in rects):
            return None
        points = [(r[0], r[1]) for r in rects] + [(r[0] + r[2], r[1] + r[3]) for r in rects]
    else:
        return None
    if not points:
        return None
    left, top = min(p[0] for p in points), min(p[1] for p in points)
    width, height = max(p[0] for p in points) - left, max(p[1] for p in points) - top
    return (left, top, width, height) if min(width, height) > 0 else None


def content_units(layers) -> dict[str, tuple[PanelContentUnit, ...]]:
    result = {}
    layers = tuple(layers)
    owners, content_for_group = {}, {}
    for layer in layers:
        if not hasattr(layer, "texture_version"):
            continue
        for obj in layer.vector_objects:
            payload = obj.meta.get(PANEL_KEY)
            frame = read_frame_layer(payload.get("metadata")) if payload else None
            if frame:
                owners[payload["panel_id"]] = frame.content_group_id
                content_for_group[frame.panel_group_id] = frame.content_group_id
    for layer in layers:
        if not hasattr(layer, "texture_version"):
            continue  # Persistence snapshots supply dialogue, not editable units.
        marker = (getattr(layer, "metadata", None) or {}).get(PART_KEY, {})
        if marker.get("role") in {"content", "overflow"}:
            key = (
                layer.id if marker["role"] == "content" else content_for_group.get(layer.parent_id)
            )
            children = layer.children
        elif FRONT_KEY in (layer.metadata or {}):
            key = owners.get(layer.metadata[FRONT_KEY].get("panel_id"))
            children = (layer,)
        else:
            continue
        if key is None:
            continue
        result[key] = result.get(key, ()) + tuple(
            PanelContentUnit(
                c.id, c.parent_id, ContentBounds(c.id, _bounds(c)), str(c.name), c.texture_version
            )
            for c in children
        )
    return copy.deepcopy(result)
