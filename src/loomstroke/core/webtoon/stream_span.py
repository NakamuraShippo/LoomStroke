"""Where one segment lands in the virtual vertical stream (CT-12 §9)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["StreamSpan"]


@dataclass(frozen=True, slots=True)
class StreamSpan:
    """Stream rows ``[stream_y0, stream_y1)`` hold the segment's crop scaled
    by ``scale`` (= target width / crop width).  ``protected_ranges`` are the
    segment's no-split intervals already mapped to stream coordinates."""

    index: int
    page_id: str
    stream_y0: int
    stream_y1: int
    scale: float
    source_crop: tuple[int, int, int, int]
    gap_before_px: int = 0
    gap_after_px: int = 0
    protected_ranges: tuple[tuple[int, int], ...] = ()

    @property
    def height(self) -> int:
        return self.stream_y1 - self.stream_y0

    def contains(self, stream_y: int) -> bool:
        return self.stream_y0 <= stream_y < self.stream_y1

    def overlaps(self, y0: int, y1: int) -> bool:
        return self.stream_y1 > y0 and self.stream_y0 < y1

    def stream_to_source_y(self, stream_y: int) -> int:
        """Source (uncropped page) row for a stream row inside this span."""
        _x, crop_y, _w, crop_h = self.source_crop
        local = int((stream_y - self.stream_y0) / self.scale)
        return crop_y + max(0, min(crop_h - 1, local))
