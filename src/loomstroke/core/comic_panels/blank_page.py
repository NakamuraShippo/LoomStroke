"""Build an independent blank page without flattening editable comic frames."""

from __future__ import annotations

import copy

from .membership import FRONT_KEY, PANEL_KEY, PART_KEY, SHARED_KEY, UNIT_KEY, subtree
from .page_copy import remap_copied_page


def blank_comic_layers(source_layers, *, page_id, new_layer_id):
    """Return a detached frame skeleton, or None for the ordinary blank path.

    Frame ink, restoration sources, clipping masks and carriers are structural.
    Page artwork is cleared while its empty layer/group organization remains.
    The normal complete-page remapper fixes all materialized frame identities.
    """
    sources = [node for root in source_layers for node in subtree(root)]
    if not any(
        PANEL_KEY in (getattr(obj, "meta", None) or {})
        for node in sources
        for obj in node.vector_objects or ()
    ):
        return None
    layers = copy.deepcopy(list(source_layers))
    for node in (node for root in layers for node in subtree(root)):
        node.id = new_layer_id()
        metadata = node.metadata or {}
        role = metadata.get(PART_KEY, {}).get("role")
        ink = role is not None and role not in {
            "panel",
            "content",
            "content_bundle",
            "retired_panel",
        }
        if not ink:
            node.image.fill(0)
            node.vector_objects = [
                obj for obj in node.vector_objects or () if PANEL_KEY in (obj.meta or {})
            ]
            for key in (FRONT_KEY, SHARED_KEY, UNIT_KEY, "loomstroke.comictools.panel_gate"):
                metadata.pop(key, None)
            # Blank content does not carry generation results or external assets.
            node.ai_effect = node.origin = node.scene_3d = None
            node.camera_3d = node.lighting_3d = node.gen_3d = None
            node.effect_type = None
            node.effect_params = {}
            node.effect_mask_image = None
            if hasattr(node, "native_cmyk"):
                node.native_cmyk = None
            if node.layer_type not in {"group", "raster", "vector", "mask"}:
                node.layer_type = "raster"
    remap_copied_page(source_layers, layers, page_id=page_id)
    return layers
