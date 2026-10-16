"""One laid-out line of a text paragraph as code-point indices (CT-03)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextLineSpan:
    """``text[start:end]`` is drawn; the next line starts at ``next_start``.

    ``next_start`` skips the break character(s) a line consumed (a space at a
    soft break, the newline at a hard break) so consumers never have to guess
    offsets again.  ``hanging`` marks a line whose last character was allowed
    past the measure limit (ぶら下げ); ``forced`` marks a break that had to
    ignore the policy because nothing breakable fit; ``hard`` marks a break
    at an explicit newline (or the end of the text).
    """

    start: int
    end: int
    next_start: int
    hard: bool = False
    forced: bool = False
    hanging: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start

    def text(self, content: str) -> str:
        return content[self.start : self.end]
