"""Read legacy frames and materialized carriers through one page adapter."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from loomstroke.core.comic_panels.materialize_command import PART_NAMESPACE
from loomstroke.core.comic_panels.models import PanelFrameError

from .model import LAYER_ROLE_FRAMES, PanelRecord, layer_comic_role, panel_record_from_object


def read_panel_sources(
    layers: Iterable[Any],
) -> tuple[dict[str, PanelRecord], dict[str, tuple[str, Any]], tuple[str, ...]]:
    records, sources, issues = {}, {}, []
    for layer in layers:
        part = (getattr(layer, "metadata", None) or {}).get(PART_NAMESPACE, {})
        carrier = isinstance(part, dict) and part.get("role") == "carrier"
        if layer_comic_role(layer) != LAYER_ROLE_FRAMES and not carrier:
            continue
        for original in getattr(layer, "vector_objects", None) or []:
            obj = copy.deepcopy(original)
            record = panel_record_from_object(obj)
            if record is None:
                continue
            if record.panel_id in records:
                issues.append(f"duplicate panel record: {record.panel_id}")
                continue
            records[record.panel_id] = record
            sources[record.panel_id] = layer.id, obj
    return records, sources, tuple(issues)


def populate_frame_regions(state, page_size):
    """Derive editable geometry on the detached read-state, never the document."""
    from . import layout_tree as lt  # noqa: PLC0415
    from .panel_convex_geometry import has_inclined_cuts  # noqa: PLC0415
    from .panel_effective_convex import effective_convex_panels  # noqa: PLC0415
    from .panel_frame_layout_adapter import layout_snapshot  # noqa: PLC0415

    if state.layout is None:
        return
    state.panel_rects = lt.compute_panel_rects(state.layout, *page_size)
    if has_inclined_cuts(state.layout.root) or any(
        r.metadata.get("frame_layer", {}).get("geometry_mode") == "layout_tree_convex"
        for r in state.panel_records.values()
    ):
        try:
            state.panel_polygons = effective_convex_panels(
                state.layout, state.panel_records, page_size
            )
            state.panel_rects = {pid: p.rect for pid, p in state.panel_polygons.items()}
        except PanelFrameError as exc:
            state.panel_issues += (str(exc),)
    if not state.panel_polygons and any(
        "frame_layer" in r.metadata for r in state.panel_records.values()
    ):
        try:
            snapshot = layout_snapshot(state.layout, page_size, state.panel_records)
            state.panel_rects = {r.panel_id: r.rect for r in snapshot.regions}
        except PanelFrameError as exc:
            state.panel_issues += (str(exc),)
    state.reading_order = lt.normalized_reading_order(state.layout, state.panel_rects)
