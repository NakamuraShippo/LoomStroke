"""Apply a prepared viewport change through the existing geometry history port."""

import copy

from .canvas_crop_plan import prepare_frame_canvas_crop
from .canvas_transform_plan import install_frame_page_attributes


def resize_managed_canvas(geometry, size, anchor):
    canvas = geometry._canvas
    width, height = size
    ax, ay = geometry.normalize_anchor(anchor)
    shift = round(ax * (width - canvas._width)), round(ay * (height - canvas._height))
    prepared = prepare_frame_canvas_crop(canvas, size, shift)
    before = geometry.take_resize_snapshot()
    selection = copy.deepcopy(getattr(canvas, "_vector_selection", None))
    try:
        install_frame_page_attributes(canvas, prepared)
        canvas._width, canvas._height = size
        geometry._clear_geometry_selection_state()
        geometry._reset_composited(*size)
        geometry._finish_geometry_mutation(reset_composited=False)
        after = geometry.take_resize_snapshot()
    except Exception:
        canvas._restore_resize_snapshot(before)
        canvas._vector_selection = selection
        raise
    geometry._push_resize_history("Resize Canvas", before, after)
    geometry._emit_geometry_changed("resize", 0, 0, *size)
