"""Effective rectangular panels derived from base leaves, independent of tree history."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from itertools import pairwise

from .models import PanelFrameError, PanelRegion, Rect, validate_rect


def intersection_area(a: Rect, b: Rect) -> float:
    return max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    )


def _bounds(rects: Sequence[Rect]) -> Rect:
    left, top = min(r[0] for r in rects), min(r[1] for r in rects)
    return left, top, max(r[0] + r[2] for r in rects) - left, max(r[1] + r[3] for r in rects) - top


def _covered(bounds: Rect, rects: Sequence[Rect], gutter: float) -> bool:
    """Sweep expanded leaves; only internal gutter may fill their seams."""
    x, y, w, h = bounds
    expanded = [
        (
            max(x, r[0] - gutter / 2),
            max(y, r[1] - gutter / 2),
            min(x + w, r[0] + r[2] + gutter / 2),
            min(y + h, r[1] + r[3] + gutter / 2),
        )
        for r in rects
    ]
    cuts = sorted({x, x + w, *(v for r in expanded for v in (r[0], r[2]))})
    tolerance = 1e-7 * max(1.0, w, h)
    for left, right in pairwise(cuts):
        if right - left <= tolerance:
            continue
        mid = (left + right) / 2
        spans = sorted((r[1], r[3]) for r in expanded if r[0] <= mid <= r[2])
        end = y
        for start, stop in spans:
            if start > end + tolerance:
                return False
            end = max(end, stop)
        if end < y + h - tolerance:
            return False
    return True


def resolve_regions(
    leaves: Mapping[str, Rect],
    owners: Mapping[str, Sequence[str]],
    *,
    gutter_px: float,
) -> tuple[PanelRegion, ...]:
    """Every base leaf has exactly one effective owner; no hidden third panel."""
    if not math.isfinite(gutter_px) or gutter_px < 0:
        raise PanelFrameError("invalid panel gutter")
    for rect in leaves.values():
        validate_rect(rect)
    used: set[str] = set()
    result: list[PanelRegion] = []
    for panel_id, leaf_ids in owners.items():
        ids = tuple(leaf_ids)
        if (
            not ids
            or len(set(ids)) != len(ids)
            or used.intersection(ids)
            or not set(ids) <= leaves.keys()
        ):
            raise PanelFrameError("invalid or duplicate leaf ownership")
        used.update(ids)
        rects = [leaves[p] for p in ids]
        bounds = _bounds(rects)
        if not _covered(bounds, rects, gutter_px):
            raise PanelFrameError("effective panel contains a hole or is not rectangular")
        if any(intersection_area(bounds, r) > 1e-7 for p, r in leaves.items() if p not in ids):
            raise PanelFrameError("effective panel would include another panel")
        result.append(PanelRegion(panel_id, ids, bounds))
    if used != set(leaves):
        raise PanelFrameError("unowned layout leaf")
    return tuple(result)


def merge_regions(
    leaves: Mapping[str, Rect],
    owners: Mapping[str, Sequence[str]],
    primary_id: str,
    secondary_id: str,
    *,
    gutter_px: float,
) -> tuple[PanelRegion, ...]:
    """Plan a merge without rebuilding the base tree or moving unrelated panels."""
    resolve_regions(leaves, owners, gutter_px=gutter_px)
    if primary_id == secondary_id or primary_id not in owners or secondary_id not in owners:
        raise PanelFrameError("merge requires two distinct known panels")
    updated = {p: tuple(ids) for p, ids in owners.items() if p != secondary_id}
    updated[primary_id] = tuple(owners[primary_id]) + tuple(owners[secondary_id])
    return resolve_regions(leaves, updated, gutter_px=gutter_px)
