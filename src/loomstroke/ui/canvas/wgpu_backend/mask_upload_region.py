"""Conservative coverage bounds for nearest-sampled comic ownership textures."""

import math


def nearest_mask_upload_region(mask_size, canvas_size, region):
    if region is None or min(*mask_size, *canvas_size) <= 0:
        return None
    x, y = region.dest_origin
    w, h = region.dest_size
    if not all(math.isfinite(value) for value in (x, y, w, h)) or min(w, h) <= 0:
        return None
    mw, mh = mask_size
    cw, ch = canvas_size
    # Include the clamped source texels outside the page, and a sampling guard.
    left = max(0, min(mw - 1, math.floor(x * mw / cw) - 1))
    top = max(0, min(mh - 1, math.floor(y * mh / ch) - 1))
    right = max(left + 1, min(mw, math.ceil((x + w) * mw / cw) + 1))
    bottom = max(top + 1, min(mh, math.ceil((y + h) * mh / ch) + 1))
    return left, top, right - left, bottom - top
