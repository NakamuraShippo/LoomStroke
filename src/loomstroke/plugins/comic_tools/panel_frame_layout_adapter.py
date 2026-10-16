"""Bridge the existing split tree to CT-01's effective region/edge planner.

The tree remains the geometry authority. No tree/schema mutation is performed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import BoundaryRef, PanelFrameError, PanelRegion, Side
from loomstroke.core.comic_panels.regions import resolve_regions

from . import layout_tree as lt
from .model import PanelRecord


@dataclass(frozen=True, slots=True)
class PanelLayoutSnapshot:
    regions: tuple[PanelRegion, ...]
    boundaries: tuple[tuple[str, Side, BoundaryRef], ...]


def layout_snapshot(
    layout: lt.ComicPageLayout,
    page_size: tuple[float, float],
    records: Mapping[str, PanelRecord],
    *,
    owners_override: Mapping[str, Sequence[str]] | None = None,
) -> PanelLayoutSnapshot:
    leaves = lt.compute_panel_rects(layout, *page_size)
    owners: dict[str, Sequence[str]] = {}
    for panel_id, record in records.items():
        if record.panel_id != panel_id:
            raise PanelFrameError("mismatched panel record identity")
        frame = read_frame_layer(record.metadata)
        owners[panel_id] = frame.layout_leaf_ids if frame is not None else (panel_id,)
    if not records:
        owners = {panel_id: (panel_id,) for panel_id in leaves}
    if owners_override is not None:
        owners.update(owners_override)
    regions = resolve_regions(leaves, owners, gutter_px=layout.gutter_px)
    boundaries: list[tuple[str, Side, BoundaryRef]] = []
    page = {side: BoundaryRef("page", side) for side in ("top", "right", "bottom", "left")}
    _boundaries(layout.root, page, boundaries)
    return PanelLayoutSnapshot(regions, tuple(boundaries))


def _boundaries(node, inherited, result):
    if node.is_leaf:
        for side, ref in inherited.items():
            result.append((node.panel_id, side, ref))
        return
    first_side, last_side = (
        ("left", "right") if node.orientation == "vertical" else ("top", "bottom")
    )
    for index, child in enumerate(node.children):
        sides = dict(inherited)
        if index > 0:
            sides[first_side] = BoundaryRef("cut", "after", node.node_id, index - 1)
        if index < len(node.children) - 1:
            sides[last_side] = BoundaryRef("cut", "before", node.node_id, index)
        _boundaries(child, sides, result)
