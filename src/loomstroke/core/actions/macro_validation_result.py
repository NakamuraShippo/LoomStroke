"""Outcome of validating a macro against a catalog."""

from __future__ import annotations

from dataclasses import dataclass

from .macro_issue import MacroIssue

__all__ = ["MacroValidationResult"]


@dataclass(frozen=True, slots=True)
class MacroValidationResult:
    issues: tuple[MacroIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(issue.is_error for issue in self.issues)

    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    def for_step(self, step_id: str) -> tuple[MacroIssue, ...]:
        return tuple(issue for issue in self.issues if issue.step_id == step_id)
