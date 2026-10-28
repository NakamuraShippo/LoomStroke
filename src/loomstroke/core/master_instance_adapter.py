"""Opt standard master commands into prepared mutation and book completion."""

from __future__ import annotations

import logging
from typing import Any

from .canvas_history_clear_continuation import _HistoryClearContinuation
from .canvas_model_recovery import _CanvasModelRecovery
from .master_instance_mutation import _prepare_master_instance_mutation
from .master_instance_preparation import _MasterPreparation, _supports_standard_master
from .master_instance_transaction import _MasterInstanceOutcome, _MasterInstanceTransaction
from .page_command_completion import _current_page_completion

logger = logging.getLogger(__name__)


def _unavailable_checked(service: Any, command: Any) -> bool | None:
    if not command._requires_checked_master:
        return None
    report = getattr(service, "_emit_page_op_failed", None)
    if callable(report):
        page_id = command.affected_page_ids[0] if command.affected_page_ids else ""
        report("master", page_id, "checked master execution is unavailable")
    return False


def _prepared_current(step: Any) -> bool:
    return (
        step.canonical is not None
        and step._same_scope()
        and step.page.layers is step.original
        and (step.live is None or (step.canvas._layers is step.live and step._same_history()))
    )


def _apply_master_instances_checked(
    service: Any, command: Any, *, reverse: bool = False
) -> bool | None:
    """None keeps the existing unknown-hook route; standard commands use no private setter.

    Every prepared install is known code. Arbitrary private setter/restore
    reentry diagnostics are not used as a product fallback or rescue policy.
    """
    completion = _current_page_completion(service)
    if completion is None or not _supports_standard_master(service):
        return _unavailable_checked(service, command)
    command._requires_checked_master = True
    page_ids = tuple(transition.page_id for transition in command.transitions)
    if not page_ids:
        return False
    preparation = _MasterPreparation(service, page_ids, completion)
    residency = service._hold_page_residency()
    try:
        if len(set(page_ids)) != len(page_ids):
            preparation.report("a master target page appears more than once")
            return False
        activated = completion.activate()
        if not activated:
            preparation.report("master page changed before command activation")
        if not activated or not preparation.run():
            return False
        return _run_prepared(service, command, preparation, completion, reverse=reverse)
    except Exception as exc:
        logger.exception("Failed to prepare checked master instances")
        preparation.report(str(exc))
        return False
    finally:
        residency.release()


def _run_prepared(
    service: Any, command: Any, preparation: _MasterPreparation, completion: Any, *, reverse: bool
) -> bool:
    canvas = service._canvas
    history = _HistoryClearContinuation(canvas)
    stacks = tuple((stack, tuple(stack)) for stack in (canvas._undo_stack, canvas._redo_stack))
    steps = []
    for transition in command.transitions:
        state = service._find_page_state(transition.page_id)
        step = _prepare_master_instance_mutation(
            service,
            transition.page_id,
            remove_layer=transition.after_layer if reverse else transition.before_layer,
            insert_layer=transition.before_layer if reverse else transition.after_layer,
            insert_index=transition.before_index if reverse else transition.after_index,
            captured_layers=state.page.layers if transition.page_id == preparation.active else None,
        )
        if step is None:
            preparation.report("master mutation is not available")
            return False
        steps.append(step)

    def ready() -> bool:
        return (
            preparation.current()
            and preparation.scope.is_ready()
            and history.is_current()
            and all(
                len(stack) == len(groups)
                and all(a is b for a, b in zip(stack, groups, strict=True))
                for stack, groups in stacks
            )
        )

    if not ready() or not all(_prepared_current(step) for step in steps):
        preparation.report("master candidate changed during preparation")
        return False
    transaction = _MasterInstanceTransaction(
        service,
        command.transitions,
        reverse=reverse,
        canvas_recovery_factory=_CanvasModelRecovery,
        prepared_steps=steps,
        completion=completion,
        final_guard=ready,
    )
    outcome = transaction.run()
    if outcome is not _MasterInstanceOutcome.COMMITTED:
        if outcome is _MasterInstanceOutcome.RECOVERY_PENDING:
            logger.critical("Checked master instance recovery did not complete")
        preparation.report("master instance operation did not complete")
        return False
    # Direct staging never invokes an unknown semantic override while the
    # model is provisional. Delivery uses the existing completion after seal.
    completion.stage("_emit_document_changed", ("pages.master_instances_changed",), failure=False)
    completion.stage("_emit_pages_changed", (), failure=False)
    return True
