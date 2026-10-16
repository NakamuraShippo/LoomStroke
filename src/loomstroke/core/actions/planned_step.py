"""One step of a dry-run plan: resolved target, bound params, effects, readiness."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .action_effect import ActionEffect
from .macro_step import MacroStep
from .resolved_target import ResolvedTarget

__all__ = ["PlannedStep", "StepStatus"]

StepStatus = Literal["ready", "blocked", "disabled"]


def _no_target() -> ResolvedTarget:
    return ResolvedTarget("none")


@dataclass(frozen=True, slots=True)
class PlannedStep:
    step: MacroStep
    status: StepStatus
    target: ResolvedTarget = field(default_factory=_no_target)
    params: Mapping[str, Any] = field(default_factory=dict)
    effects: tuple[ActionEffect, ...] = ()
    reason_code: str = ""
    warnings: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    @property
    def requires_confirmation(self) -> bool:
        return any(effect.confirmation_required for effect in self.effects)

    def effect_kinds(self) -> tuple[str, ...]:
        return tuple(sorted({effect.kind for effect in self.effects}))
