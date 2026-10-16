"""Japanese typesetting primitives (CT-03): kinsoku policy, break opportunities,
line breaker, spans and layout diagnostics.  Pure and Qt-free."""

from .break_opportunities import BreakOpportunityFinder
from .kinsoku_policy import KinsokuPolicy
from .line_breaker import JapaneseLineBreaker, Measure
from .text_issue import TextIssue
from .text_layout_validator import TextLayoutValidator
from .text_line_span import TextLineSpan

__all__ = [
    "BreakOpportunityFinder",
    "JapaneseLineBreaker",
    "KinsokuPolicy",
    "Measure",
    "TextIssue",
    "TextLayoutValidator",
    "TextLineSpan",
]
