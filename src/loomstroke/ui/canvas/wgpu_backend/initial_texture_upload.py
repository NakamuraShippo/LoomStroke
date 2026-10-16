"""Upload only proved initial ink into a newly zero-initialized texture."""

from PySide6.QtCore import QRect


def upload_initial_or_full(manager, layer, handle, version, convert_full):
    image = layer.image
    rect = getattr(layer, "initial_dirty_rect", None)
    if rect is not None and getattr(layer, "initial_upload_key", None) == (
        version,
        image.cacheKey(),
    ):
        initial = manager._region_converter.convert_region(image, QRect(*rect))
        if initial is not None:
            return manager._upload_partial_region(handle, initial[0], initial[1], version)
    return manager._upload_full(handle, convert_full(image), version)
