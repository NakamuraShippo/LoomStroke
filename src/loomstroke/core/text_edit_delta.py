"""Find one contiguous replacement using unchanged Python string indices."""

from __future__ import annotations


def changed_text_span(
    previous: str, current: str, cursor_position: int | None
) -> tuple[int, int, int]:
    """Return old start/end and new end for a contiguous editor replacement.

    A supplied cursor is the Python string index after the edit, allowing
    repeated characters to be distinguished without a Qt or plugin dependency.
    """
    if cursor_position is not None:
        new_end = max(0, min(len(current), cursor_position))
        old_end = new_end - (len(current) - len(previous))
        if 0 <= old_end <= len(previous) and previous[old_end:] == current[new_end:]:
            start = 0
            while start < min(old_end, new_end) and previous[start] == current[start]:
                start += 1
            return start, old_end, new_end
    start = 0
    common_length = min(len(previous), len(current))
    while start < common_length and previous[start] == current[start]:
        start += 1
    old_end, new_end = len(previous), len(current)
    while old_end > start and new_end > start and previous[old_end - 1] == current[new_end - 1]:
        old_end -= 1
        new_end -= 1
    return start, old_end, new_end
