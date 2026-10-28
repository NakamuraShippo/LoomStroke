"""Layer facts the rules may read (CT-16 §7 — no live Qt objects)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LayerPreflightSnapshot"]


@dataclass(frozen=True, slots=True)
class LayerPreflightSnapshot:
    """``has_pixels`` is ``None`` when the content was not inspected."""

    layer_id: str
    name: str
    layer_type: str = "raster"
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    has_pixels: bool | None = None
    bounds: tuple[int, int, int, int] | None = None
    effect_type: str | None = None
    object_count: int = 0
    bleed_content_bounds: tuple[int, int, int, int] | None = None
    bleed_bounds_checked: bool = False
