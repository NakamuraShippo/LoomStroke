"""One output slice of the stream (CT-12 §9)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SlicePlan"]


@dataclass(frozen=True, slots=True)
class SlicePlan:
    """Stream rows ``[stream_y0, stream_y1)``; ``confidence`` < 1 means the
    bottom boundary was not checked against protected content and must be
    confirmed in the preview (spec 12 §7.2).  ``warnings`` are stable codes."""

    index: int
    stream_y0: int
    stream_y1: int
    confidence: float = 1.0
    warnings: tuple[str, ...] = ()

    @property
    def height(self) -> int:
        return self.stream_y1 - self.stream_y0
