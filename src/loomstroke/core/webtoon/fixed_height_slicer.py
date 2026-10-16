"""Phase W2 slicing: fixed (balanced) heights, no content analysis (CT-12 §16).

The stream is cut into ``ceil(height / max_height)`` slices of as equal a
height as possible (the first ``height % n`` slices are one row taller),
so every slice respects ``max_height``, no slice is a sliver, and the
slices tile the stream with no overlap or hole.  Boundaries are *not*
checked against panels / text (that is W3), so every internal boundary
carries ``boundary_unverified`` and confidence 0.5 — the preview must be
confirmed (spec 12 §7.2).  A boundary that lands inside a user no-split
range additionally reports ``boundary_in_no_split_range``.
"""

from __future__ import annotations

import math

from .slice_plan import SlicePlan
from .vertical_stream_plan import VerticalStreamPlan

__all__ = ["FixedHeightSlicer"]


class FixedHeightSlicer:
    """Deterministic balanced slicing of a stream."""

    UNVERIFIED: str = "boundary_unverified"
    IN_NO_SPLIT: str = "boundary_in_no_split_range"

    def plan(
        self, stream: VerticalStreamPlan, *, max_height: int, min_height: int = 1
    ) -> tuple[SlicePlan, ...]:
        if max_height < 1:
            raise ValueError("max_height must be positive")
        if min_height < 1 or min_height > max_height:
            raise ValueError("min_height must be within [1, max_height]")
        height = stream.height
        if height <= 0:
            return ()
        count = max(1, math.ceil(height / max_height))
        base, extra = divmod(height, count)
        slices: list[SlicePlan] = []
        y = 0
        for index in range(count):
            slice_height = base + (1 if index < extra else 0)
            y0, y1 = y, y + slice_height
            warnings: list[str] = []
            confidence = 1.0
            if y1 < height:
                confidence = 0.5
                warnings.append(self.UNVERIFIED)
                if self._inside_protected(stream, y1):
                    confidence = 0.0
                    warnings.append(self.IN_NO_SPLIT)
            if slice_height < min_height:
                warnings.append("slice_below_min_height")
            slices.append(SlicePlan(index, y0, y1, confidence, tuple(warnings)))
            y = y1
        return tuple(slices)

    @staticmethod
    def _inside_protected(stream: VerticalStreamPlan, boundary: int) -> bool:
        span = stream.span_at(boundary)
        if span is None:
            return False
        return any(start < boundary < end for start, end in span.protected_ranges)
