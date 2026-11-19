"""Synchronize a cached layer with the changed tiles of a paint session."""

from dataclasses import replace

from .types import WgpuTextureUploadReport


def paint_image_key(layer):
    """A replaced/filtered snapshot cannot vouch for its original paint tiles."""
    if getattr(layer, "paint_upload", None) is None:
        return layer.image.cacheKey()
    key = layer.image.cacheKey()
    return key if getattr(layer, "paint_upload_key", None) == (layer.texture_version, key) else -1


def sync_paint_tiles(manager, handle, layer, resources_invalidated, convert_full):
    image = layer.image
    version = layer.texture_version
    key = paint_image_key(layer)
    updated = replace(
        handle,
        texture_version=version,
        image_key=key,
        paint_display=getattr(layer, "paint_upload", None) is not None,
        offset_x=int(getattr(layer, "offset_x", 0)),
        offset_y=int(getattr(layer, "offset_y", 0)),
    )
    report = WgpuTextureUploadReport(layer.id, version, uploaded=False)
    regions = None
    if not resources_invalidated and handle.image_key != -1:
        if version == handle.texture_version and handle.image_key == image.cacheKey():
            regions = ()
        elif updated.paint_display and key != -1:
            receipt = layer.paint_upload
            if (
                handle.texture_version != receipt.base_version
                or handle.image_key == receipt.base_key
            ):
                regions = receipt.rects_since(handle.texture_version)
    try:
        if regions is not None:
            for rect in regions:
                converted = manager._region_converter.convert_region(image, rect)
                if converted is None or converted[1] != rect:
                    regions = None
                    break
                report = manager._upload_partial_region(handle, *converted, version)
        if regions is None:
            report = manager._upload_full(updated, convert_full(image), version)
    except Exception:
        # A partially written texture no longer represents the previous version either.
        manager.remove(layer.id)
        raise
    manager._textures[layer.id] = updated
    return updated, report, ()
