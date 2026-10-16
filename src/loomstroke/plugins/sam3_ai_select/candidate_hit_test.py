"""Canvas-space hit test for SAM3 AI Select candidate overlays.

Maps a canvas click onto the candidate under the cursor so prompt-mode
clicks can move the tool selection (2026-07-13 selection clarity). Prefers
an actual mask-pixel hit over plain bbox containment so overlapping bboxes
resolve to the shape the user really clicked; loading a mask is best-effort
and silently falls back to the bbox test.
"""

from __future__ import annotations

import logging
from typing import Any

from ...ai_selection import (
    Sam3AssetStore,
    Sam3CandidateOverlayItem,
    Sam3SelectionOverlayModel,
    load_selection_mask_qimage,
)

_ALPHA_HIT_THRESHOLD = 127

logger = logging.getLogger(__name__)


def hit_test_candidate(
    overlay_model: Sam3SelectionOverlayModel,
    asset_store: Sam3AssetStore | None,
    x: float,
    y: float,
) -> str | None:
    """Return the candidate id under canvas point ``(x, y)``, if any.

    Mask-pixel hits win over bbox-only hits; ties resolve to the smallest
    bbox area (nested shapes stay clickable), then the candidate rank.
    """

    bbox_hits = [
        item
        for item in overlay_model.visible_items
        if item.state != "rejected" and _bbox_contains(item.clipped_bbox, x, y)
    ]
    if not bbox_hits:
        return None

    mask_hits = [
        item for item in bbox_hits if _mask_alpha_hit(overlay_model, asset_store, item, x, y)
    ]
    pool = mask_hits if mask_hits else bbox_hits
    best = min(pool, key=_hit_sort_key)
    return best.candidate_id


def _hit_sort_key(item: Sam3CandidateOverlayItem) -> tuple[int, int]:
    bbox = item.clipped_bbox
    area = bbox[2] * bbox[3] if bbox is not None else 0
    return (area, item.rank)


def _bbox_contains(bbox: tuple[int, int, int, int] | None, x: float, y: float) -> bool:
    if bbox is None:
        return False
    bx, by, bw, bh = bbox
    return bx <= x < bx + bw and by <= y < by + bh


def _mask_alpha_hit(
    overlay_model: Sam3SelectionOverlayModel,
    asset_store: Sam3AssetStore | None,
    item: Sam3CandidateOverlayItem,
    x: float,
    y: float,
) -> bool:
    if asset_store is None or not item.mask_asset_id:
        return False
    try:
        asset = asset_store.load_mask_asset(item.mask_asset_id)
        if asset.source_candidate_id and asset.source_candidate_id != item.candidate_id:
            return False
        mask = load_selection_mask_qimage(asset_store, asset)
        origin = _mask_origin(overlay_model, asset, mask)
        if origin is None:
            return False
        local_x = int(x) - origin[0]
        local_y = int(y) - origin[1]
        if not (0 <= local_x < mask.width() and 0 <= local_y < mask.height()):
            return False
        return mask.pixelColor(local_x, local_y).alpha() > _ALPHA_HIT_THRESHOLD
    except Exception:
        logger.debug("SAM3 AI Select candidate hit test mask load failed", exc_info=True)
        return False


def _mask_origin(
    overlay_model: Sam3SelectionOverlayModel,
    asset: Any,
    mask: Any,
) -> tuple[int, int] | None:
    """Mirror the overlay painter's origin rule: full-canvas or bbox crop."""

    source_width = int(overlay_model.source_ref.width)
    source_height = int(overlay_model.source_ref.height)
    if (
        source_width > 0
        and source_height > 0
        and mask.width() == source_width
        and mask.height() == source_height
    ):
        return (0, 0)
    bbox_x, bbox_y, width, height = (int(value) for value in asset.bbox)
    if width <= 0 or height <= 0:
        return None
    if mask.width() != width or mask.height() != height:
        return None
    return (bbox_x, bbox_y)


__all__ = ["hit_test_candidate"]
