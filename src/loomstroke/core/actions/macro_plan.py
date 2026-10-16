"""Dry-run result for one macro on one page (CT-20 §8.1 ``MacroPlan``)."""

from __future__ import annotations

from dataclasses import dataclass

from .planned_step import PlannedStep

__all__ = ["MacroPlan"]


@dataclass(frozen=True, slots=True)
class MacroPlan:
    macro_id: str
    macro_name: str
    page_id: str
    steps: tuple[PlannedStep, ...] = ()
    validation_codes: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.validation_codes

    @property
    def is_ready(self) -> bool:
        return self.is_valid and all(
            step.is_ready for step in self.steps if step.status != "disabled"
        )

    def ready_steps(self) -> tuple[PlannedStep, ...]:
        return tuple(step for step in self.steps if step.is_ready)

    def blocked_steps(self) -> tuple[PlannedStep, ...]:
        return tuple(step for step in self.steps if step.status == "blocked")

    @property
    def requires_confirmation(self) -> bool:
        return any(step.requires_confirmation for step in self.ready_steps())

    def effect_kinds(self) -> tuple[str, ...]:
        kinds: set[str] = set()
        for step in self.ready_steps():
            kinds.update(step.effect_kinds())
        return tuple(sorted(kinds))

    def outputs(self) -> tuple[str, ...]:
        return tuple(output for step in self.ready_steps() for output in step.outputs)

    def counts(self) -> dict[str, int]:
        counts = {"ready": 0, "blocked": 0, "disabled": 0}
        for step in self.steps:
            counts[step.status] = counts.get(step.status, 0) + 1
        return counts

    def report_lines(self) -> tuple[str, ...]:
        """Secret-free text report: target -> step -> status (CT-20 §12)."""
        lines = [f"page {self.page_id}: {self.macro_name} ({self.macro_id})"]
        if self.validation_codes:
            lines.append("  invalid: " + ", ".join(self.validation_codes))
        for step in self.steps:
            effects = "/".join(step.effect_kinds()) or "-"
            detail = f" [{step.reason_code}]" if step.reason_code else ""
            target = ",".join(step.target.ids) or step.step.target.describe()
            lines.append(
                f"  {step.step.step_id} {step.step.action_id} -> {target}: "
                f"{step.status}{detail} effects={effects}"
            )
            for output in step.outputs:
                lines.append(f"    write {output}")
            for warning in step.warnings:
                lines.append(f"    warning {warning}")
        return tuple(lines)
