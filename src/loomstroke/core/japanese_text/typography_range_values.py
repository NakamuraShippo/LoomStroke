"""Forward-only partial style lookup preserving the first matching run."""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping, Sequence
from typing import Any


class RangeValueCursor:
    """Sweep ordered text without scanning all style runs for every grapheme."""

    def __init__(self, runs: Sequence[Mapping[str, Any]] | None, key: str, default: float) -> None:
        self.default = default
        events = []
        for order, run in enumerate(runs or ()):
            try:
                start, end = int(run["start"]), int(run["end"])
                value = float(run.get(key, default))
            except (TypeError, ValueError, OverflowError, KeyError):
                continue
            if end > start:
                events.append((start, order, end, value if math.isfinite(value) else default))
        self._events = sorted(events)
        self._cursor = 0
        self._active: list[tuple[int, int, float]] = []

    def at(self, start: int, end: int | None = None) -> float:
        """First original-order run intersecting an advancing half-open span."""
        end = start + 1 if end is None else end
        while self._cursor < len(self._events) and self._events[self._cursor][0] < end:
            _, order, stop, value = self._events[self._cursor]
            heapq.heappush(self._active, (order, stop, value))
            self._cursor += 1
        while self._active and self._active[0][1] <= start:
            heapq.heappop(self._active)
        return self._active[0][2] if self._active else self.default
