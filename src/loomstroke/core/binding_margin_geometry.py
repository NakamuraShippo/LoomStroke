"""Pure binding-side margin positions from existing physical-profile safe guides."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .guide_layout_geometry import guide_role_edges, unit_to_pixels


def _safe_positions(guides: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    return guide_role_edges(guides, "safe", "vertical")


def binding_margin_position(
    guides: Sequence[Mapping[str, Any]],
    *,
    width_mm: float,
    dpi_x: float,
    physical_side: str,
    canvas_width: float,
) -> float | None:
    if not math.isfinite(width_mm) or width_mm < 0:
        raise ValueError("Invalid binding margin")
    if physical_side not in ("left", "right"):
        raise ValueError("Invalid physical page side")
    if width_mm == 0:
        return None
    left, right = _safe_positions(guides)
    delta = unit_to_pixels(width_mm, "mm", dpi_x)
    if not 0 <= left < right <= canvas_width or delta >= right - left:
        raise ValueError("Binding margin leaves no safe area")
    return left + delta if physical_side == "right" else right - delta


def current_binding_margin_mm(
    guides: Sequence[Mapping[str, Any]], *, dpi_x: float, physical_side: str
) -> float:
    entries = [
        guide
        for guide in guides
        if str(guide.get("id", "")).startswith("tpl-gutter-")
        and guide.get("orientation") == "vertical"
    ]
    if len(entries) != 1:
        return 0.0
    try:
        left, right = _safe_positions(guides)
        position = float(entries[0]["position"])
    except (ValueError, TypeError, KeyError):
        return 0.0
    distance = position - left if physical_side == "right" else right - position
    return max(0.0, distance / unit_to_pixels(1, "mm", dpi_x)) if math.isfinite(distance) else 0.0
