"""Transient proof of initially transparent pixels and panel-only paint history."""

from PySide6.QtCore import QRect

from .membership import owner_for_parent


def remember_blank_source(layer):
    layer._panel_blank_source = (layer.texture_version, layer.image.cacheKey())


def expand_blank_source(layer, old_image, new_image):
    proof = getattr(layer, "_panel_blank_source", None)
    if proof is not None and proof == (layer.texture_version, old_image.cacheKey()):
        layer._panel_blank_source = (layer.texture_version, new_image.cacheKey())


def begin_blank_upload(layer):
    proof = getattr(layer, "_panel_blank_source", None)
    layer._panel_blank_source = None
    return proof is not None and proof == (layer.texture_version, layer.image.cacheKey())


def finish_blank_upload(layer, bounds, eligible):
    if eligible:
        local = QRect(bounds)
        local.translate(-layer.offset_x, -layer.offset_y)
        layer._panel_initial_upload = (
            layer.texture_version,
            layer.image.cacheKey(),
            (local.x(), local.y(), local.width(), local.height()),
        )


def initial_upload_rect(layer):
    proof = getattr(layer, "_panel_initial_upload", None)
    image = getattr(layer, "image", None)
    if (
        proof is not None
        and image is not None
        and proof[:2] == (layer.texture_version, image.cacheKey())
    ):
        return proof[2]
    return None


def panel_paint_history_only(canvas, layer_id, patch_type):
    if not callable(getattr(canvas, "_layer_tree", None)):
        return False
    group = canvas._active_history_group
    layer = canvas._get_layer_by_id(layer_id) if layer_id else None
    if (
        layer is None
        or layer.layer_type != "raster"
        or not group
        or not group.entries
        or not owner_for_parent(canvas._layer_tree(), layer.parent_id)
    ):
        return False
    return all(
        (
            isinstance(entry, patch_type)
            and entry.layer_id == layer_id
            and entry.metadata.get("command") == "paint"
            and not entry.metadata.get("mask_edit")
        )
        or (
            getattr(getattr(entry, "command", None), "raster_stroke_command", False)
            and entry.command.layer_id == layer_id
        )
        for entry in group.entries
    )
