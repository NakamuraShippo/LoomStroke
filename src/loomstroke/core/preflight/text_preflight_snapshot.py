"""Text object facts the rules may read (CT-16 §5 Text).

``overflow`` / ``font_available`` are tri-state: ``None`` means the
snapshot builder could not determine it, and the rule must report
``not checked`` rather than pass (spec 16 §5).  ``content`` is kept only
as a length + emptiness proxy in reports — rules never copy it into an
issue (spec 16 §13).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TextPreflightSnapshot"]


@dataclass(frozen=True, slots=True)
class TextPreflightSnapshot:
    object_id: str
    layer_id: str
    content: str
    font_family: str = ""
    font_size_pt: float = 24.0
    text_mode: str = "point"
    vertical: bool = False
    bounds: tuple[float, float, float, float] | None = None
    overflow: bool | None = None
    font_available: bool | None = None
    missing_font_families: tuple[str, ...] = ()
    font_families: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.content.strip()
