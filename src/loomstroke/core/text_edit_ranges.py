"""Map existing text style ranges across a contiguous Unicode edit."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def shift_text_ranges(
    entries: Any, *, start: int, old_end: int, new_end: int, text_length: int
) -> Any:
    """Preserve each range's attributes, inheriting at the replacement start."""
    if not isinstance(entries, (list, tuple)):
        return entries
    removed = old_end - start
    inserted = new_end - start

    def after_deletion(index: int) -> int:
        if index <= start:
            return index
        return max(start, index - removed)

    shifted = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            shifted.append(entry)
            continue
        try:
            old_start, old_stop = int(entry["start"]), int(entry["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            shifted.append(entry)
            continue
        if removed:
            inherits = old_start <= start < old_stop
        else:
            inherits = old_start < start <= old_stop or (start == old_start == 0)
        left, right = after_deletion(old_start), after_deletion(old_stop)
        if left > start or (left == start and not inherits):
            left += inserted
        if right > start or (right == start and inherits):
            right += inserted
        left = max(0, min(text_length, left))
        right = max(left, min(text_length, right))
        if left < right:
            shifted.append(dict(entry, start=left, end=right))
    return shifted
