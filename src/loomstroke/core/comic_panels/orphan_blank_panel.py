"""Recognize the old blank-page raster artifact without unlocking real frames."""

from .membership import PART_KEY


def is_orphan_blank_panel(query, layer):
    """Only a root raster with no structure or incoming references can qualify."""
    marker = (layer.metadata or {}).get(PART_KEY)
    if (
        not isinstance(marker, dict)
        or type(marker.get("version")) is not int
        or marker != {"version": 1, "role": "panel"}
        or layer.layer_type != "raster"
        or layer.parent_id is not None
        or layer.children
        or layer.vector_objects
    ):
        return False

    def references(value):
        if isinstance(value, dict):
            return any(references(key) or references(item) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(references(item) for item in value)
        return isinstance(value, str) and value == layer.id

    for other in query.flatten_layers():
        if other.id == layer.id:
            continue
        if other.parent_id == layer.id or references(other.metadata):
            return False
        if any(references(obj.meta) for obj in other.vector_objects or ()):
            return False
    return True
