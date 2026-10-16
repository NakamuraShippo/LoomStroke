"""Runs a plan on one page through catalog executors (CT-20 §9).

Mutations happen inside one history transaction per page (``undo_grouping``
"page") or one per step; cancel is honoured between steps only, never
mid-step; blocked steps follow the step's ``on_error`` policy.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .action_catalog import ActionCatalog
from .action_context import ActionContext
from .macro_definition import UndoGrouping
from .macro_plan import MacroPlan
from .macro_run_result import MacroRunResult
from .planned_step import PlannedStep
from .step_outcome import StepOutcome

logger = logging.getLogger(__name__)

__all__ = ["MacroExecutor"]

CancelCheck = Callable[[], bool]


class MacroExecutor:
    def __init__(self, catalog: ActionCatalog) -> None:
        self._catalog = catalog

    def execute(
        self,
        plan: MacroPlan,
        context: ActionContext,
        *,
        cancel: CancelCheck | None = None,
        undo_grouping: UndoGrouping = "page",
        transaction_label: str = "",
    ) -> MacroRunResult:
        if not plan.is_valid:
            return MacroRunResult(plan.macro_id, plan.page_id, stopped_reason="macro_invalid")
        outcomes: list[StepOutcome] = []
        stopped = ""
        cancelled = False
        label = transaction_label or f"Macro: {plan.macro_name}"
        grouped = undo_grouping == "page" and any(
            step.is_ready and any(e.kind == "mutation" for e in step.effects) for step in plan.steps
        )
        if grouped:
            self._begin(context, label)
        try:
            for planned in plan.steps:
                if planned.status == "disabled":
                    continue
                if cancel is not None and cancel():
                    cancelled = True
                    outcomes.append(self._outcome(planned, "cancelled"))
                    break
                if not planned.is_ready:
                    outcomes.append(self._outcome(planned, "blocked", planned.reason_code))
                    if planned.step.on_error == "stop":
                        stopped = planned.reason_code or "step_blocked"
                        break
                    continue
                outcome = self._run_step(planned, context, label, undo_grouping)
                outcomes.append(outcome)
                if not outcome.ok and planned.step.on_error == "stop":
                    stopped = outcome.reason_code or "step_failed"
                    break
        finally:
            if grouped:
                self._end(context)
        return MacroRunResult(plan.macro_id, plan.page_id, tuple(outcomes), cancelled, stopped)

    # -- internals ---------------------------------------------------------------------

    def _run_step(
        self, planned: PlannedStep, context: ActionContext, label: str, grouping: UndoGrouping
    ) -> StepOutcome:
        executor = self._catalog.executor(planned.step.action_id)
        if executor is None:
            return self._outcome(planned, "blocked", "action_unavailable")
        per_step = grouping == "step" and any(e.kind == "mutation" for e in planned.effects)
        if per_step:
            self._begin(context, f"{label} / {planned.step.step_id}")
        try:
            result = executor(context, planned.target, planned.params)
        except Exception as error:  # adapters must not take the whole macro down
            logger.warning("action %s failed", planned.step.action_id, exc_info=True)
            return self._outcome(planned, "failed", "action_exception", type(error).__name__)
        finally:
            if per_step:
                self._end(context)
        if result.ok:
            return StepOutcome(
                planned.step.step_id, planned.step.action_id, "success", outputs=result.outputs
            )
        return self._outcome(
            planned, "failed", result.reason_code or "action_failed", result.detail
        )

    @staticmethod
    def _outcome(
        planned: PlannedStep, status: str, reason: str = "", detail: str = ""
    ) -> StepOutcome:
        return StepOutcome(
            planned.step.step_id, planned.step.action_id, status, reason, detail  # type: ignore[arg-type]
        )

    @staticmethod
    def _begin(context: ActionContext, label: str) -> None:
        begin = getattr(context.canvas, "begin_transaction", None)
        if callable(begin):
            begin(label)

    @staticmethod
    def _end(context: ActionContext) -> None:
        end = getattr(context.canvas, "end_transaction", None)
        if callable(end):
            end()
