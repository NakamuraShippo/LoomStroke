"""Page facts the rules may read (CT-16 §7)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .layer_preflight_snapshot import LayerPreflightSnapshot
from .text_preflight_snapshot import TextPreflightSnapshot

__all__ = ["PagePreflightSnapshot"]


@dataclass(frozen=True, slots=True)
class PagePreflightSnapshot:
    """``hydrated`` is False when only metadata was available (CT-08 placeholder)."""

    page_id: str
    index: int
    name: str
    width: int
    height: int
    dpi: tuple[float, float]
    role: str = "body"
    layers: tuple[LayerPreflightSnapshot, ...] = field(default_factory=tuple)
    texts: tuple[TextPreflightSnapshot, ...] = field(default_factory=tuple)
    has_physical_layout: bool = False
    hydrated: bool = True

    @property
    def visible_content_layers(self) -> tuple[LayerPreflightSnapshot, ...]:
        return tuple(layer for layer in self.layers if layer.visible and layer.has_pixels)
