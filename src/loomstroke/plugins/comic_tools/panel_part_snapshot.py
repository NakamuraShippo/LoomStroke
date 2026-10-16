"""Read-only part snapshots shared by layout and edge command planners."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.comic_panels.membership import FRONT_KEY, PART_KEY, SHARED_KEY

from .model import layer_comic_role
from .raster_parts import part_layer_balloon_id

FIELDS = (
    "vector_objects",
    "metadata",
    "layer_mask",
    "effect_mask",
    "mask_enabled",
    "transform",
    "image",
    "layer_type",
    "offset_x",
    "offset_y",
    "visible",
    "locked",
    "vector_kind",
    "mask_edit_mode",
    "opacity",
    "blend_mode",
    "clipping",
    "effect_type",
    "_preview_transform",
)


@dataclass(frozen=True)
class PanelPartSnapshot:
    layer_id: str
    parent_id: str | None
    children: tuple[str, ...]
    values: dict[str, Any]
    texture_version: int


def part_snapshots(layers, *, include_layer_ids=()) -> dict[str, PanelPartSnapshot]:
    result = {}
    layers = tuple(layers)
    lookup = {layer.id: layer for layer in layers}

    def managed(layer):
        seen = set()
        while layer:
            if layer.id in seen:
                return False
            seen.add(layer.id)
            if (
                {FRONT_KEY, PART_KEY, SHARED_KEY} & (getattr(layer, "metadata", None) or {}).keys()
                or layer_comic_role(layer)
                or part_layer_balloon_id(layer)
            ):
                return True
            layer = lookup.get(layer.parent_id)
        return False

    included = {
        layer.id
        for layer in layers
        if hasattr(layer, "texture_version") and (layer.id in include_layer_ids or managed(layer))
    }
    # An ordinary parent can gate a managed subtree without having comic metadata.
    for layer_id in tuple(included):
        ancestor = lookup[layer_id]
        seen = {layer_id}
        while ancestor.parent_id and ancestor.parent_id not in seen:
            seen.add(ancestor.parent_id)
            ancestor = lookup.get(ancestor.parent_id)
            if ancestor is None:
                break
            included.add(ancestor.id)
    for layer in layers:
        # Page persistence snapshots have no live parent/revision/mask state;
        # they are read-only sources for dialogue tables, never edit targets.
        if not hasattr(layer, "texture_version"):
            continue
        if layer.id not in included:
            continue
        values = {}
        for field in FIELDS:
            # Saved page snapshots expose only persisted fields. Read-only
            # dialogue inspection must not assume the live canvas mask/cache API.
            if not hasattr(layer, field):
                continue
            value = getattr(layer, field)
            # Qt implicit sharing keeps read_state cheap. Commands detach before writing.
            values[field] = QImage(value) if isinstance(value, QImage) else copy.deepcopy(value)
        result[layer.id] = PanelPartSnapshot(
            layer.id,
            layer.parent_id,
            tuple(c.id for c in layer.children),
            values,
            getattr(layer, "texture_version", 0),
        )
    return result
