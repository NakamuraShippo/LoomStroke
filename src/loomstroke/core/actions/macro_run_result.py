"""Result of executing a macro plan on one page (CT-20 §8.2 ``MacroRunResult``)."""

from __future__ import annotations

from dataclasses import dataclass

from .step_outcome import StepOutcome

__all__ = ["MacroRunResult"]


@dataclass(frozen=True, slots=True)
class MacroRunResult:
    macro_id: str
    page_id: str
    outcomes: tuple[StepOutcome, ...] = ()
    cancelled: bool = False
    stopped_reason: str = ""

    @property
    def ok(self) -> bool:
        return (
            not self.cancelled
            and not self.stopped_reason
            and all(outcome.status in {"success", "skipped"} for outcome in self.outcomes)
        )

    @property
    def executed_any(self) -> bool:
        return any(outcome.ok for outcome in self.outcomes)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return counts

    def outputs(self) -> tuple[str, ...]:
        return tuple(output for outcome in self.outcomes for output in outcome.outputs)
