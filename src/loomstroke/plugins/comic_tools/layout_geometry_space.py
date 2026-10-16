"""Keep a split tree's geometry fixed when only the page viewport changes."""

import copy
import math

from loomstroke.core.comic_panels.canvas_transform_basis import page_matrix
from loomstroke.core.comic_panels.models import PanelFrameError

SPACE_KEY = "geometry_canvas_basis"


def geometry_space(metadata, page_size):
    basis = metadata.get(SPACE_KEY)
    if basis is None:
        return tuple(page_size), (0.0, 0.0)
    if not isinstance(basis, dict):
        raise PanelFrameError("invalid comic layout canvas basis")
    size, origin = basis.get("reference_size"), basis.get("origin")
    if (
        basis.get("version") != 1
        or not isinstance(size, (list, tuple))
        or len(size) != 2
        or not isinstance(origin, (list, tuple))
        or len(origin) != 2
        or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in (*size, *origin))
        or min(size) <= 0
    ):
        raise PanelFrameError("invalid comic layout canvas basis")
    return tuple(size), tuple(origin)


def layout_page_rect(layout, page_size):
    (width, height), (ox, oy) = geometry_space(layout.metadata, page_size)
    margin = max(0.0, float(layout.margin_px))
    return ox + margin, oy + margin, max(1.0, width - 2 * margin), max(1.0, height - 2 * margin)


def translated_space(metadata, page_size, dx, dy):
    size, origin = geometry_space(metadata, page_size)
    result = copy.deepcopy(metadata)
    basis = result.setdefault(SPACE_KEY, {})
    basis.update(
        {
            "version": 1,
            "reference_size": list(size),
            "origin": [origin[0] + dx, origin[1] + dy],
        }
    )
    return result


def reorient_space(metadata, page_size, mode):
    if SPACE_KEY not in metadata:
        return copy.deepcopy(metadata)
    size, origin = geometry_space(metadata, page_size)
    current, reference = page_matrix(mode, *page_size), page_matrix(mode, *size)
    mapped, zero = current.apply(*origin), reference.apply(0, 0)
    result = copy.deepcopy(metadata)
    result[SPACE_KEY].update(
        {
            "version": 1,
            "reference_size": list(size[::-1] if mode in {"rot90", "rot-90"} else size),
            "origin": [mapped[0] - zero[0], mapped[1] - zero[1]],
        }
    )
    return result


def scaled_space(metadata, page_size, factor):
    size, origin = geometry_space(metadata, page_size)
    if SPACE_KEY in metadata:
        metadata[SPACE_KEY].update(
            {
                "version": 1,
                "reference_size": [v * factor for v in size],
                "origin": [v * factor for v in origin],
            }
        )
