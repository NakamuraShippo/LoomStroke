"""Page switch orchestration shared by the public facade and checked book edits."""

from __future__ import annotations

import logging
from collections.abc import Callable
from inspect import getattr_static
from typing import Any, cast

from .canvas_history_clear_continuation import _HistoryClearSuperseded
from .events import Events
from .page_command_completion import _allows_page_command_commit, _stage_page_completion
from .page_mutation_preflight import PageMutationPreflight
from .page_switch_legacy import _supports_legacy_page_switch, _switch_legacy_page
from .page_switch_preparation import _prepare_outgoing_switch
from .page_switch_transaction import (
    _PageSwitchOutcome,
    _supports_page_recovery,
    _switch_with_recovery,
)

logger = logging.getLogger(__name__)


class _GuardedPageMutationPreflight(PageMutationPreflight):
    """Keep a surrounding structure edit's original state order during callbacks."""

    def __init__(self, service: Any, page_ids: tuple[str, ...], guard: Callable[[], bool]):
        super().__init__(service, page_ids)
        self._guard = guard

    def is_current(self) -> bool:
        return super().is_current() and self._guard()


def _checked_switch_preparation(service: Any, scope: PageMutationPreflight | None) -> bool:
    from .page_service import _DEFAULT_PAGE_PERSIST  # noqa: PLC0415

    return (
        scope is not None
        and service._page_history.supports_page_history()
        and getattr_static(service, "_persist_active_page", None) is _DEFAULT_PAGE_PERSIST
        and service._page_history._supports_prepared_detach()
    )


def _supports_checked_structure_switch(service: Any) -> bool:
    """Unknown public switch hooks retain their existing one-argument route."""
    from .page_service import _DEFAULT_PAGE_SET_ACTIVE  # noqa: PLC0415

    return (
        getattr_static(service, "set_active_page", None) is _DEFAULT_PAGE_SET_ACTIVE
        and _checked_switch_preparation(service, PageMutationPreflight(service, ()))
        and _supports_page_recovery(service._canvas)
    )


def _request_page_switch(  # noqa: PLR0911, PLR0912, PLR0915
    service: Any,
    page_id: str,
    *,
    failure_context: tuple[str, str] | None = None,
    require_recovery: bool = False,
    defer_settlement: bool = False,
    recover: Callable[..., _PageSwitchOutcome] = _switch_with_recovery,
    scope_guard: Callable[[], bool] | None = None,
) -> _PageSwitchOutcome | None:
    """Return a call-local checked outcome; public callers discard this result.

    ``None`` denotes an original custom route, whose public installer
    has no outcome contract. Book edits must not infer its success from an id.
    """
    recovery_kwargs = {"failure_context": failure_context} if failure_context is not None else {}
    failure_context = failure_context or ("set_active", page_id)

    def report(error: str) -> None:
        service._emit_page_op_failed(*failure_context, error)

    service._sync_document_canvas_with_live()
    state = service._find_page_state(page_id)
    if state is None:
        report("page not found")
        return _PageSwitchOutcome.REFUSED
    if service._active_page_id == page_id:
        return _PageSwitchOutcome.COMMITTED
    outgoing_id = service._active_page_id
    outgoing_state = service._find_page_state(outgoing_id or "")
    scope = (
        (
            _GuardedPageMutationPreflight(service, (outgoing_id, page_id), scope_guard)
            if scope_guard is not None
            else PageMutationPreflight(service, (outgoing_id, page_id))
        )
        if outgoing_id is not None and outgoing_state is not None
        else None
    )

    def current(active_id: str | None) -> bool:
        return _allows_page_command_commit(service) and (
            scope is None or (scope.is_current() and service._active_page_id == active_id)
        )

    hydrated = service._ensure_page_hydrated(state)
    if not current(outgoing_id):
        return _PageSwitchOutcome.SUPERSEDED
    if not hydrated:
        report("page content could not be loaded")
        return _PageSwitchOutcome.REFUSED
    if scope is None:
        outgoing_id = service._active_page_id
    per_page_history = service._page_history.supports_page_history()
    with service._history_state_notifier.batch():
        service._emit_event(
            Events.ACTIVE_PAGE_CHANGING,
            {"page_id": page_id, "previous_page_id": outgoing_id},
        )
        if not current(outgoing_id):
            return _PageSwitchOutcome.SUPERSEDED
        checked_preparation = _checked_switch_preparation(service, scope)
        recovered_switch = checked_preparation and _supports_page_recovery(service._canvas)
        legacy_switch = _supports_legacy_page_switch(service, scope)
        if require_recovery and not recovered_switch:
            report("checked page switch is unavailable")
            return _PageSwitchOutcome.REFUSED
        if recovered_switch:
            outcome = recover(
                service,
                cast(PageMutationPreflight, scope),
                cast(str, outgoing_id),
                state,
                **recovery_kwargs,
            )
            if outcome is not _PageSwitchOutcome.COMMITTED:
                if scope.is_current() and not _allows_page_command_commit(service):
                    report("book history candidate changed during preparation")
                return outcome
        elif legacy_switch:
            outcome = _switch_legacy_page(
                service,
                cast(PageMutationPreflight, scope),
                cast(str, outgoing_id),
                state,
                failure_context=failure_context,
            )
            if outcome is not _PageSwitchOutcome.COMMITTED:
                return outcome
        elif checked_preparation:
            if not _prepare_outgoing_switch(
                service,
                cast(PageMutationPreflight, scope),
                cast(str, outgoing_id),
                page_id,
                failure_context=failure_context,
            ):
                return (
                    _PageSwitchOutcome.REFUSED
                    if current(outgoing_id)
                    else _PageSwitchOutcome.SUPERSEDED
                )
        else:
            if per_page_history:
                keep = bool(outgoing_id) and service._find_page_state(outgoing_id or "") is not None
                service._page_history.detach_active_history(outgoing_id, keep=keep)
            service._persist_active_page()
        if not recovered_switch and not legacy_switch:
            service._active_page_id = page_id
        try:
            if not recovered_switch and not legacy_switch:
                service._apply_page_to_canvas(state.page)
            if not current(page_id):
                return _PageSwitchOutcome.SUPERSEDED
            if per_page_history and not recovered_switch:
                service._page_history.attach_history_for(page_id)
            elif not per_page_history and not legacy_switch:
                service._reset_history_state(reason="page_switch")
        except _HistoryClearSuperseded:
            return _PageSwitchOutcome.SUPERSEDED
        except Exception as exc:
            if not current(page_id):
                return _PageSwitchOutcome.SUPERSEDED
            logger.exception("Failed to apply page %s", page_id)
            report(str(exc))
            return _PageSwitchOutcome.REFUSED
        if not current(page_id):
            return _PageSwitchOutcome.SUPERSEDED
        service._emit_active_page_changed(page_id)
        if defer_settlement:
            # The surrounding book commit performs its normal history trim.
            # Eviction must wait until the source payload is in command custody.
            _stage_page_completion(service, "evict_pages_to_budget")
        else:
            service._book_history.request_memory_trim()
            service.evict_pages_to_budget()
        service._history_state_notifier.mark_changed()
    return _PageSwitchOutcome.COMMITTED if recovered_switch or legacy_switch else None
