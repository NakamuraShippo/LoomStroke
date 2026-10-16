"""Overflow / kinsoku diagnostics over broken lines (CT-03 段階 2).

Pure: takes the spans the breaker produced plus the frame capacity and
returns stable issues instead of silently clipping — the text tool badge,
CT-04 balloon fit and CT-16 preflight all read the same codes.
"""

from __future__ import annotations

from collections.abc import Sequence

from .grapheme_clusters import grapheme_spans
from .line_breaker import Measure
from .text_issue import TextIssue
from .text_line_span import TextLineSpan


class TextLayoutValidator:
    """Turn spans + capacity into :class:`TextIssue` entries."""

    def issues(
        self,
        text: str,
        spans: Sequence[TextLineSpan],
        *,
        measure: Measure | None = None,
        max_extent: float | None = None,
        line_capacity: int | None = None,
    ) -> tuple[TextIssue, ...]:
        """``line_capacity`` = how many lines / columns the frame can hold;
        ``max_extent`` + ``measure`` let the validator re-measure forced lines."""
        result: list[TextIssue] = []
        if line_capacity is not None and len(spans) > max(0, int(line_capacity)):
            first_hidden = spans[max(0, int(line_capacity))]
            result.append(
                TextIssue(
                    code="text_overflow_lines",
                    message=f"{len(spans)} lines do not fit {int(line_capacity)} line(s)",
                    start=first_hidden.start,
                    end=len(text),
                )
            )
        for span in spans:
            if span.forced:
                result.append(
                    TextIssue(
                        code="kinsoku_forced_break",
                        message="no break opportunity fit the line; broke mid-word / "
                        "against the policy",
                        start=span.start,
                        end=span.end,
                    )
                )
            if measure is not None and max_extent is not None and span.length > 0:
                extent = sum(
                    float(measure(start, text[start:end]))
                    for start, end in grapheme_spans(text, span.start, span.end)
                )
                if extent > float(max_extent) and not span.hanging:
                    result.append(
                        TextIssue(
                            code="text_overflow_extent",
                            message=f"line extent {extent:.1f} exceeds {float(max_extent):.1f}",
                            start=span.start,
                            end=span.end,
                        )
                    )
        return tuple(result)
