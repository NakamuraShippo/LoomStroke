"""Scope a group mask upload to the canvas pixels covered by its target."""

from contextlib import contextmanager
from types import SimpleNamespace

from loomstroke.core.appearance.live_styles import style_signature


@contextmanager
def group_mask_upload_scope(compositor, layer, region, scene):
    if style_signature(layer) or not getattr(layer, "mask_region_eligible", False):
        yield
        return
    previous = getattr(compositor, "_mask_region_request", None)
    x, y, w, h = region
    inverse = compositor._view_inverse or (1, 0, 0, 1, 0, 0)
    a, b, c, d, tx, ty = inverse
    points = [
        (a * px + b * py + tx, c * px + d * py + ty)
        for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
    ]
    xs, ys = zip(*points, strict=True)
    bounds = SimpleNamespace(
        dest_origin=(min(xs), min(ys)), dest_size=(max(xs) - min(xs), max(ys) - min(ys))
    )
    compositor._mask_region_request = (layer, bounds, (scene.width, scene.height))
    try:
        yield
    finally:
        compositor._mask_region_request = previous
