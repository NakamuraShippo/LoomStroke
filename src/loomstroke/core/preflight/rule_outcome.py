"""What one rule produced for one scan (CT-16 §14: never a silent pass)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .preflight_issue import PreflightIssue

__all__ = ["RuleOutcome"]


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """``not_checked`` carries a sanitized ``reason_code`` (no exception text)."""

    rule_id: str
    status: Literal["checked", "not_checked"]
    issues: tuple[PreflightIssue, ...] = ()
    reason_code: str | None = None
