"""Runs one macro over many targets in deterministic order (CT-20 §6.3 / §9).

Each target is planned (dry run) right before it executes, using a fresh
``ActionContext`` from *context_factory*; ``before_target`` lets the UI switch
the active page.  Cancel stops before the next target; the remaining ids are
reported for re-run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .action_context import ActionContext
from .batch_run_result import BatchPolicy, BatchRunResult
from .batch_target_record import BatchTargetRecord
from .macro_definition import MacroDefinition
from .macro_executor import MacroExecutor
from .macro_planner import MacroPlanner

__all__ = ["BatchRunner"]

ContextFactory = Callable[[str], ActionContext]
TargetHook = Callable[[str], None]
CancelCheck = Callable[[], bool]


class BatchRunner:
    def __init__(self, planner: MacroPlanner, executor: MacroExecutor) -> None:
        self._planner = planner
        self._executor = executor

    def run(
        self,
        definition: MacroDefinition,
        targets: Sequence[str],
        context_factory: ContextFactory,
        *,
        bindings: Mapping[str, Any] | None = None,
        policy: BatchPolicy = "stop",
        cancel: CancelCheck | None = None,
        before_target: TargetHook | None = None,
        after_target: TargetHook | None = None,
    ) -> BatchRunResult:
        records: list[BatchTargetRecord] = []
        cancelled = False
        stopped = ""
        for index, target_id in enumerate(targets):
            if cancelled or stopped:
                records.append(BatchTargetRecord(target_id, "not_started"))
                continue
            if cancel is not None and cancel():
                cancelled = True
                records.append(BatchTargetRecord(target_id, "cancelled"))
                continue
            if before_target is not None:
                before_target(target_id)
            try:
                record = self._run_target(
                    definition, target_id, context_factory, bindings, policy, cancel
                )
            finally:
                if after_target is not None:
                    after_target(target_id)
            records.append(record)
            if record.run is not None and record.run.cancelled:
                cancelled = True
            elif record.status in {"failed", "blocked"} and policy == "stop":
                stopped = record.reason_code or record.status
            del index
        return BatchRunResult(policy, tuple(records), cancelled, stopped)

    def _run_target(
        self,
        definition: MacroDefinition,
        target_id: str,
        context_factory: ContextFactory,
        bindings: Mapping[str, Any] | None,
        policy: BatchPolicy,
        cancel: CancelCheck | None,
    ) -> BatchTargetRecord:
        context = context_factory(target_id)
        plan = self._planner.plan(definition, context, bindings)
        if not plan.is_valid:
            return BatchTargetRecord(target_id, "blocked", plan.validation_codes[0])
        if not plan.is_ready:
            blocked = plan.blocked_steps()
            reason = blocked[0].reason_code if blocked else "step_blocked"
            if policy == "skip_blocked":
                return BatchTargetRecord(target_id, "skipped", reason)
            if policy == "stop":
                return BatchTargetRecord(target_id, "blocked", reason)
        run = self._executor.execute(
            plan, context, cancel=cancel, undo_grouping=definition.undo_grouping
        )
        if run.cancelled:
            status = "cancelled"
        elif run.ok:
            status = "success"
        elif run.executed_any:
            status = "partial"
        else:
            status = "failed"
        reason = run.stopped_reason or next(
            (o.reason_code for o in run.outcomes if o.reason_code), ""
        )
        return BatchTargetRecord(target_id, status, reason, run)  # type: ignore[arg-type]
