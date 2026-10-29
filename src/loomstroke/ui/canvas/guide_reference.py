"""Read existing selection frames without changing pixels or selection history."""

from __future__ import annotations

import math
from typing import Any

from ...core.composition_geometry import reference_rect


def _rect(bounds: Any, *, corners: bool = False) -> tuple | None:
    if bounds is None:
        return None
    if hasattr(bounds, "isEmpty"):
        if bounds.isEmpty():
            return None
        values = bounds.x(), bounds.y(), bounds.width(), bounds.height()
    elif isinstance(bounds, (tuple, list)) and len(bounds) == 4:
        try:
            values = tuple(map(float, bounds))
        except (TypeError, ValueError):
            return None
        if corners:
            values = values[0], values[1], values[2] - values[0], values[3] - values[1]
    else:
        return None
    return values if all(math.isfinite(v) for v in values) and min(values[2:]) > 0 else None


def _object_reference_rect(canvas):
    selection = getattr(canvas, "_vector_selection", None)
    if isinstance(selection, dict):
        bounds = _rect(selection.get("transform_bounds"), corners=True)
        if bounds:
            return bounds
        ids = set(selection.get("ids", []))
        ids.update(item.get("id") for item in selection.get("items", []) if isinstance(item, dict))
        ids.add(selection.get("id"))
        ids.discard(None)
        getter = getattr(canvas, "get_vector_objects", None)
        finder = getattr(canvas, "_get_layer_by_id", None)
        if callable(finder) and selection.get("layer_id"):
            layer = finder(selection["layer_id"])
            from ...core.command_layer_access import _layer_transform  # noqa: PLC0415
            from ...core.command_types import LayerTransform  # noqa: PLC0415

            objects = getattr(layer, "vector_objects", [])
            if (
                layer is None
                or _layer_transform(layer) != LayerTransform.identity()
                or len(objects) > 512
            ):
                return None
            candidates = [(selection["layer_id"], obj) for obj in objects]
        else:
            candidates = getter() if callable(getter) else []
        if len(candidates) <= 512:
            from ...plugins.vector_tools.common import compute_vector_bounds  # noqa: PLC0415

            objects = [obj for _, obj in candidates if obj.id in ids]
            if {obj.id for obj in objects} != ids or sum(
                len(p.anchors) for obj in objects for p in obj.paths
            ) > 4096:
                return None
            boxes = [compute_vector_bounds(obj) for obj in objects]
            boxes = [box for box in boxes if box is not None]
            if boxes:
                x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
                return _rect(
                    (x, y, max(b[2] for b in boxes), max(b[3] for b in boxes)), corners=True
                )
    return None


def guide_reference_rect(widget: Any, reference: str) -> tuple | None:
    host = getattr(widget, "_host_api", None)
    canvas = getattr(host, "canvas", None)
    if reference == "object":
        return _object_reference_rect(canvas)
    selection = _rect(getattr(canvas, "_selection_bounds", None))
    guides = []
    pages = getattr(host, "pages", None)
    if reference == "trim" and pages is not None:
        state = pages.get_page_view_state(pages.get_active_page_id()) or {}
        raw = state.get("guides", [])
        guides = [g for g in raw if isinstance(g, dict)] if isinstance(raw, list) else []
    return reference_rect(
        (widget.state.width, widget.state.height), reference, guides=guides, selection=selection
    )
