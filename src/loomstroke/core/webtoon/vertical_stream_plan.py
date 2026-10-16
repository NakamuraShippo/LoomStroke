"""The virtual vertical stream: spans in order, one shared width (CT-12 §9)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .stream_span import StreamSpan

__all__ = ["VerticalStreamPlan"]


@dataclass(frozen=True, slots=True)
class VerticalStreamPlan:
    """``height`` includes leading/trailing gaps; rows outside every span are gap."""

    width: int
    height: int
    spans: tuple[StreamSpan, ...] = field(default_factory=tuple)

    @property
    def page_ids(self) -> tuple[str, ...]:
        return tuple(span.page_id for span in self.spans)

    def span_at(self, stream_y: int) -> StreamSpan | None:
        for span in self.spans:
            if span.contains(stream_y):
                return span
        return None

    def spans_between(self, y0: int, y1: int) -> tuple[StreamSpan, ...]:
        return tuple(span for span in self.spans if span.overlaps(y0, y1))

    def span_for_page(self, page_id: str) -> StreamSpan | None:
        for span in self.spans:
            if span.page_id == page_id:
                return span
        return None

    def stream_to_local(self, stream_y: int) -> tuple[str, int] | None:
        """``(page_id, source_y)`` for a stream row, ``None`` inside a gap."""
        span = self.span_at(stream_y)
        if span is None:
            return None
        return span.page_id, span.stream_to_source_y(stream_y)

    def local_to_stream(self, page_id: str, source_y: int) -> int | None:
        """Stream row for a source page row (clamped to the span)."""
        span = self.span_for_page(page_id)
        if span is None:
            return None
        _x, crop_y, _w, _h = span.source_crop
        offset = math.floor((source_y - crop_y) * span.scale + 0.5)
        return span.stream_y0 + max(0, min(span.height - 1, offset))
