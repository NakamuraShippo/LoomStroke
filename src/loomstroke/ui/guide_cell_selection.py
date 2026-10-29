"""Select a clicked guide cell using the existing selection command path."""

from __future__ import annotations

from typing import Any

from ..core.commands import SelectionOp, UpdateSelectionMaskCommand
from ..core.guide_cell_geometry import guide_cell_rect


def guide_cell_candidate(
    window: Any, expected_page_id: str | None
) -> tuple[int, int, int, int] | None:
    host = getattr(window, "_host_api", None)
    pages = getattr(host, "pages", None)
    controller = getattr(getattr(window, "canvas", None), "_guide_controller", None)
    context = getattr(window, "_last_canvas_context", {})
    point = context.get("canvas_pos") if isinstance(context, dict) else None
    if pages is None or controller is None or not controller.guides_visible:
        return None
    if expected_page_id is None or pages.get_active_page_id() != expected_page_id:
        return None
    if not isinstance(point, (tuple, list)) or len(point) != 2:
        return None
    try:
        coordinates = (float(point[0]), float(point[1]))
    except (TypeError, ValueError):
        return None
    spec = pages.get_active_canvas_spec()
    return guide_cell_rect(
        (
            (g.orientation, g.position)
            for g in controller.guides()
            if not getattr(g, "rotation", 0) % 360
        ),
        (spec.width, spec.height),
        coordinates,
    )


def select_guide_cell(window: Any, expected_page_id: str | None) -> bool:
    rect = guide_cell_candidate(window, expected_page_id)
    if rect is None:
        return False
    canvas = getattr(getattr(window, "_host_api", None), "canvas", None)
    if canvas is None:
        return False
    return bool(canvas.submit(UpdateSelectionMaskCommand(rect, SelectionOp.REPLACE)))
