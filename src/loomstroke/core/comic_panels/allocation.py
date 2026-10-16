"""Deterministic split allocation; copies/shared sources remain explicit actions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import ContentBounds, PanelFrameError, PanelRegion
from .regions import intersection_area


@dataclass(frozen=True, slots=True)
class ContentAllocation:
    item_id: str
    panel_id: str
    reason: str
    unresolved: bool = False


def allocate_split(
    items: Sequence[ContentBounds],
    children: Sequence[PanelRegion],
    original_panel_id: str,
    *,
    overrides: Mapping[str, str] | None = None,
) -> tuple[ContentAllocation, ...]:
    """Largest bounds overlap wins; ties/unknown/outside retain the original ID.

    A manual assignment always wins, including during another boundary drag.
    This plans references only; no pixels, transforms, masks or groups change.
    """
    targets = {p.panel_id for p in children}
    item_ids = {i.item_id for i in items}
    if len(targets) != len(children) or original_panel_id not in targets or len(children) < 2:
        raise PanelFrameError("split requires distinct children and original panel identity")
    if len(item_ids) != len(items):
        raise PanelFrameError("duplicate split content")
    overrides = dict(overrides or {})
    if not overrides.keys() <= item_ids or not set(overrides.values()) <= targets:
        raise PanelFrameError("invalid manual split allocation")
    return tuple(_allocate(item, children, original_panel_id, overrides) for item in items)


def _allocate(item, children, original, overrides) -> ContentAllocation:
    if item.item_id in overrides:
        return ContentAllocation(item.item_id, overrides[item.item_id], "manual")
    if item.bounds is None:
        return ContentAllocation(item.item_id, original, "unknown_bounds", True)
    areas = [(p.panel_id, intersection_area(item.bounds, p.rect)) for p in children]
    maximum = max(area for _, area in areas)
    if maximum <= 0:
        return ContentAllocation(item.item_id, original, "outside", True)
    winners = [p for p, area in areas if math.isclose(area, maximum, rel_tol=1e-9, abs_tol=1e-7)]
    if len(winners) != 1:
        return ContentAllocation(item.item_id, original, "tie", True)
    return ContentAllocation(item.item_id, winners[0], "overlap")
