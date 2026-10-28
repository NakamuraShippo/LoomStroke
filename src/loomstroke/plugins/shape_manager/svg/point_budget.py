"""Local anchor accounting and incremental SVG points decoding."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass


@dataclass
class SvgPointBudget:
    limit: int
    used: int = 0

    def consume(self, count: int = 1) -> None:
        if self.used + count > self.limit:
            raise ValueError("SVG exceeds supported anchor count")
        self.used += count


def iter_point_pairs(
    data: str, pattern: re.Pattern[str], parse_number: Callable[[str], float]
) -> Iterator[tuple[float, float]]:
    """Preserve numeric extraction without constructing a complete float list."""
    first: float | None = None
    for match in pattern.finditer(data):
        token = match.group()
        if token.isalpha():
            continue
        number = parse_number(token)
        if first is None:
            first = number
        else:
            yield (first, number)
            first = None
    if first is not None:
        raise ValueError("SVG points require complete coordinate pairs")
