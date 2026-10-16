"""Deterministic kinsoku-aware line breaker (CT-03 stages 1-2).

Greedy first-fit over a paragraph: characters are accumulated with a caller
supplied ``measure(index, char)`` until the next one would exceed the limit;
the line then ends at the last break opportunity (or is forced when none
fit).  A hanging punctuation mark (ぶら下げ) may sit past the limit instead
of moving to the next line.  Works for horizontal (advance) and vertical
(char step) layout alike because the geometry is abstracted behind
``measure`` and ``max_extent``.
"""

from __future__ import annotations

from collections.abc import Callable

from .break_opportunities import BreakOpportunityFinder
from .grapheme_clusters import grapheme_spans
from .kinsoku_policy import KinsokuPolicy
from .text_line_span import TextLineSpan

Measure = Callable[[int, str], float]


class JapaneseLineBreaker:
    """Break text into :class:`TextLineSpan` lists."""

    def __init__(self, finder: BreakOpportunityFinder | None = None) -> None:
        self._finder = finder or BreakOpportunityFinder()

    def break_text(
        self,
        text: str,
        measure: Measure,
        max_extent: float,
        *,
        policy: KinsokuPolicy | None = None,
        wrap_mode: str = "word",
    ) -> list[TextLineSpan]:
        """Spans for all paragraphs; ``measure`` receives a whole grapheme."""
        rules = policy or KinsokuPolicy.standard()
        mode = str(wrap_mode).strip().lower()
        limit = max(1.0, float(max_extent))
        spans: list[TextLineSpan] = []
        for start, end, next_start in self.paragraphs(text):
            if mode == "none" or end == start:
                spans.append(TextLineSpan(start, end, next_start, hard=True))
                continue
            spans.extend(
                self._break_paragraph(
                    text, start, end, next_start, measure, limit, rules=rules, mode=mode
                )
            )
        return spans or [TextLineSpan(0, 0, 0, hard=True)]

    @staticmethod
    def paragraphs(text: str) -> list[tuple[int, int, int]]:
        """``(start, end, next_start)`` per paragraph; ``\\r\\n`` / ``\\r`` / ``\\n`` break."""
        result: list[tuple[int, int, int]] = []
        start = 0
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if char == "\n":
                result.append((start, index, index + 1))
                start = index + 1
            elif char == "\r":
                skip = 2 if index + 1 < length and text[index + 1] == "\n" else 1
                result.append((start, index, index + skip))
                start = index + skip
                index += skip
                continue
            index += 1
        result.append((start, length, length))
        return result

    def _break_paragraph(
        self,
        text: str,
        start: int,
        end: int,
        paragraph_next: int,
        measure: Measure,
        limit: float,
        *,
        rules: KinsokuPolicy,
        mode: str,
    ) -> list[TextLineSpan]:
        allowed = self._finder.opportunities(text, start, end, policy=rules, wrap_mode=mode)
        cluster_ends = dict(grapheme_spans(text, start, end))
        spans: list[TextLineSpan] = []
        line_start = start
        width = 0.0
        last_opportunity: int | None = None
        index = start
        while index < end:
            next_index = cluster_ends[index]
            char = text[index:next_index]
            advance = float(measure(index, char))
            if index > line_start and width + advance > limit:
                # ぶら下げ: one punctuation mark may hang past the limit.
                can_hang = (
                    rules.can_hang(char)
                    and not (index > start and rules.can_hang(text[index - 1]))
                    and (next_index == end or allowed.get(next_index, True))
                )
                if can_hang:
                    spans.append(TextLineSpan(line_start, next_index, next_index, hanging=True))
                    line_start = next_index
                    width = 0.0
                    last_opportunity = None
                    index = next_index
                    continue
                if last_opportunity is not None and last_opportunity > line_start:
                    break_at = last_opportunity
                    forced = False
                else:
                    break_at = index
                    forced = True
                line_end = break_at
                if line_end > line_start and text[line_end - 1] == " ":
                    line_end -= 1
                spans.append(TextLineSpan(line_start, line_end, break_at, forced=forced))
                line_start = break_at
                width = 0.0
                last_opportunity = None
                index = break_at
                continue
            width += advance
            if allowed.get(next_index, False) and next_index < end:
                last_opportunity = next_index
            index = next_index
        spans.append(TextLineSpan(line_start, end, paragraph_next, hard=True))
        return spans
