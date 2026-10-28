"""Prepare a standard switch source before transferring its history ownership."""

from __future__ import annotations

import logging
from typing import Any

from .canvas_history_clear_continuation import _HistoryClearSuperseded
from .page_command_completion import _allows_page_command_commit
from .page_mutation_preflight import PageMutationPreflight
from .page_switch_recovery import _PageSwitchRecovery

logger = logging.getLogger(__name__)


def _prepare_outgoing_switch(
    service: Any,
    scope: PageMutationPreflight,
    outgoing_id: str,
    target_id: str,
    *,
    retain_state: bool = False,
    failure_context: tuple[str, str] | None = None,
) -> bool | _PageSwitchRecovery:
    """Close/capture the source; atomic callers detach inside their receipt boundary."""

    def same_scope() -> bool:
        return (
            scope.is_current()
            and service._active_page_id == outgoing_id
            and _allows_page_command_commit(service)
        )

    try:
        close_pending = getattr(service._canvas, "_history_close_pending_group", None)
        closed = close_pending() if callable(close_pending) else None
        if not same_scope():
            return False
        if closed is False:
            raise RuntimeError("pending stroke could not be closed")
        service._persist_active_page(raise_on_error=True, scope_guard=same_scope)
        if not same_scope():
            return False
        if retain_state and (
            service._canvas._in_transaction or service._canvas._active_history_group is not None
        ):
            raise RuntimeError("prepared history detach refused: history is still pending")
        recovery = _PageSwitchRecovery(service, outgoing_id, target_id) if retain_state else None
        if recovery is None:
            service._page_history._detach_prepared_active_history(outgoing_id, keep=True)
        # The atomic controller includes detach (and its notifications) in the
        # recovery boundary; legacy/custom callers keep their old bool result.
        return recovery if recovery is not None else True
    except _HistoryClearSuperseded:
        if retain_state:
            raise
        return False
    except Exception as exc:
        if not same_scope():
            return False
        logger.exception("Failed to prepare outgoing page %s", outgoing_id)
        service._emit_page_op_failed(*(failure_context or ("set_active", target_id)), str(exc))
        return False
