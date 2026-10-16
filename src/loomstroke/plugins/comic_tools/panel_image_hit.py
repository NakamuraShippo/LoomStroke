"""Read-only image picking through the visible ownership clip and source pixels."""

import math

from PySide6.QtCore import QRect

from loomstroke.core.comic_panels.image_placement import PLACEMENT_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.mask_coverage import mask_coverage_alpha8


def visible_at(state, layer_id, point, page_size):
    seen = set()
    while layer_id:
        part = state.panel_parts.get(layer_id)
        if part is None or layer_id in seen:
            return False
        seen.add(layer_id)
        values = part.values
        if not values["visible"] or values["opacity"] <= 0:
            return False
        mask = values["layer_mask"]
        if mask is not None and not mask.isNull() and values["mask_enabled"]:
            x = min(mask.width() - 1, max(0, math.floor(point[0] * mask.width() / page_size[0])))
            y = min(mask.height() - 1, max(0, math.floor(point[1] * mask.height() / page_size[1])))
            coverage = mask_coverage_alpha8(mask.copy(QRect(x, y, 1, 1)))
            if coverage.pixelColor(0, 0).alpha() == 0:
                return False
        layer_id = part.parent_id
    return True


def imported_image_at(state, panel_id, point, *, page_size):
    record = state.panel_records.get(panel_id)
    frame = read_frame_layer(record.metadata) if record else None
    if frame is None:
        return None
    for unit in reversed(state.panel_contents.get(frame.content_group_id, ())):
        part = state.panel_parts.get(unit.layer_id)
        if part is None or not visible_at(state, unit.layer_id, point, page_size):
            continue
        values = part.values
        if PLACEMENT_KEY not in (values["metadata"] or {}):
            # Keep the foreground balloon/text's ordinary editing interaction.
            bounds = unit.bounds.bounds
            if bounds is None:
                return None
            x, y, width, height = bounds
            if x <= point[0] <= x + width and y <= point[1] <= y + height:
                return None
            continue
        image = values["image"]
        inverse = values["transform"].to_affine().inverse()
        if image is None or inverse is None:
            continue
        x, y = inverse.apply(*point)
        if (
            0 <= x < image.width()
            and 0 <= y < image.height()
            and image.pixelColor(math.floor(x), math.floor(y)).alpha() > 0
        ):
            return unit.layer_id
    return None
