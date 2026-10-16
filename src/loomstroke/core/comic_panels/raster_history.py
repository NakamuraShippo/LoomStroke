"""Preserve managed edge brush patches after intervening bitmap preparation Undo."""

from .membership import PART_KEY


def prepare_edge_patch_restore(canvas, layer, rect):
    marker = (layer.metadata or {}).get(PART_KEY, {})
    if marker.get("role") == "edge" and layer.layer_type == "raster":
        canvas._ensure_layer_image_bounds(layer, rect)
