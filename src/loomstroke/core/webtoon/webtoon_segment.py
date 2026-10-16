"""One episode segment: a logical page with optional crop and gaps (CT-12 §5).

Gaps are export-derived (never drawn into page pixels); ``include`` lets a
page sit in the document without joining the stream; ``no_split_ranges``
are user *No Split* intervals in source (uncropped) page pixels.  The
source size travels with the segment so stream planning never hydrates
page content (CT-08).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WebtoonSegment"]

Rect = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class WebtoonSegment:
    """``crop`` is ``(x, y, w, h)`` in source pixels; ``None`` = whole page."""

    page_id: str
    source_width: int
    source_height: int
    crop: Rect | None = None
    gap_before_px: int = 0
    gap_after_px: int = 0
    include: bool = True
    no_split_ranges: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if not self.page_id:
            raise ValueError("segment page_id is required")
        if self.source_width < 1 or self.source_height < 1:
            raise ValueError("segment source size must be positive")
        if self.gap_before_px < 0 or self.gap_after_px < 0:
            raise ValueError("segment gaps cannot be negative")
        if self.crop is not None:
            x, y, w, h = self.crop
            if w < 1 or h < 1 or x < 0 or y < 0:
                raise ValueError("segment crop must be a positive rect inside the page")
            if x + w > self.source_width or y + h > self.source_height:
                raise ValueError("segment crop exceeds the page")
        for start, end in self.no_split_ranges:
            if end <= start or start < 0 or end > self.source_height:
                raise ValueError("no-split ranges must be (start < end) inside the page")

    @property
    def effective_crop(self) -> Rect:
        return self.crop or (0, 0, self.source_width, self.source_height)

    @property
    def crop_width(self) -> int:
        return self.effective_crop[2]

    @property
    def crop_height(self) -> int:
        return self.effective_crop[3]
