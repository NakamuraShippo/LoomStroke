"""Segments -> stream coordinates (CT-12 spec 12 §9 / §10).

The single rounding rule: each included segment's crop is scaled to the
target width and its stream height is ``floor(crop_h * width / crop_w +
0.5)`` (half-up, minimum 1 row).  Gaps are added verbatim.  Spans never
overlap and never leave holes other than declared gaps, so slices cut
from the stream concatenate back to it exactly (spec 12 §17).
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from .stream_span import StreamSpan
from .vertical_stream_plan import VerticalStreamPlan
from .webtoon_segment import WebtoonSegment

__all__ = ["VerticalStreamResolver"]


class VerticalStreamResolver:
    """Build a :class:`VerticalStreamPlan` for a target width."""

    @staticmethod
    def scaled_height(crop_width: int, crop_height: int, target_width: int) -> int:
        return max(1, math.floor(crop_height * target_width / crop_width + 0.5))

    def build(self, segments: Iterable[WebtoonSegment], *, target_width: int) -> VerticalStreamPlan:
        if target_width < 1:
            raise ValueError("target width must be positive")
        spans: list[StreamSpan] = []
        y = 0
        for segment in segments:
            if not segment.include:
                continue
            crop = segment.effective_crop
            _cx, crop_y, crop_w, crop_h = crop
            scale = target_width / crop_w
            height = self.scaled_height(crop_w, crop_h, target_width)
            y += segment.gap_before_px
            protected = tuple(
                self._protected_range(start, end, crop_y, crop_h, scale, y, height)
                for start, end in segment.no_split_ranges
            )
            spans.append(
                StreamSpan(
                    index=len(spans),
                    page_id=segment.page_id,
                    stream_y0=y,
                    stream_y1=y + height,
                    scale=scale,
                    source_crop=crop,
                    gap_before_px=segment.gap_before_px,
                    gap_after_px=segment.gap_after_px,
                    protected_ranges=tuple(r for r in protected if r is not None),
                )
            )
            y += height + segment.gap_after_px
        return VerticalStreamPlan(width=target_width, height=y, spans=tuple(spans))

    @staticmethod
    def _protected_range(
        start: int,
        end: int,
        crop_y: int,
        crop_h: int,
        scale: float,
        stream_y0: int,
        height: int,
    ) -> tuple[int, int] | None:
        """Map a source no-split interval into the span (clipped to the crop)."""
        local_start = max(0, start - crop_y)
        local_end = min(crop_h, end - crop_y)
        if local_end <= local_start:
            return None
        y0 = stream_y0 + math.floor(local_start * scale + 0.5)
        y1 = stream_y0 + math.floor(local_end * scale + 0.5)
        y1 = min(stream_y0 + height, max(y1, y0 + 1))
        return (y0, y1)
