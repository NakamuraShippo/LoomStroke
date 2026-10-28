"""Checked Delete ownership transfer after the successor switch has committed."""

from __future__ import annotations

import logging
from typing import Any

from .page_command_completion import _current_page_completion
from .page_commands import DeletePageCommand
from .page_mutation_preflight import PageMutationPreflight
from .page_switch_request import _request_page_switch, _supports_checked_structure_switch
from .page_switch_transaction import _PageSwitchOutcome

logger = logging.getLogger(__name__)


def _delete_page_checked(service: Any, command: DeletePageCommand) -> bool | None:
    """Return None for unchanged public/custom routes, otherwise an exact result."""
    completion = _current_page_completion(service)
    if completion is None or not _supports_checked_structure_switch(service):
        return None
    page_id = command.page_id
    index = service._page_index(page_id)
    if len(service._pages) <= 1 or index < 0:
        error = "cannot delete last page" if len(service._pages) <= 1 else "page not found"
        service._emit_page_op_failed("delete", page_id, error)
        return False
    order = tuple(service._pages)
    scope = PageMutationPreflight(service, tuple(state.page.id for state in order))
    previous_active = service._active_page_id
    was_active = previous_active == page_id
    successor_id = (
        order[index + 1 if index + 1 < len(order) else index - 1].page.id
        if was_active
        else previous_active
    )

    def order_current() -> bool:
        return (
            scope.is_current()
            and completion.allows_commit()
            and len(service._pages) == len(order)
            and all(before is after for before, after in zip(order, service._pages, strict=True))
        )

    def current(active_id: str | None) -> bool:
        return order_current() and service._active_page_id == active_id

    lease = service._hold_page_residency()
    try:
        try:
            if not completion.activate():
                raise RuntimeError("page changed before deletion")
            if was_active:
                outcome = _request_page_switch(
                    service,
                    successor_id,
                    failure_context=("delete", page_id),
                    require_recovery=True,
                    defer_settlement=True,
                    scope_guard=order_current,
                )
                if outcome is not _PageSwitchOutcome.COMMITTED:
                    if (
                        outcome is _PageSwitchOutcome.SUPERSEDED
                        and completion.document_is_current()
                    ):
                        service._emit_page_op_failed(
                            "delete", page_id, "page changed during deletion"
                        )
                    return False
            if not current(successor_id):
                if completion.document_is_current():
                    service._emit_page_op_failed("delete", page_id, "page changed during deletion")
                return False
        except Exception as exc:
            logger.exception("Failed to prepare deletion of page %s", page_id)
            if completion.document_is_current():
                service._emit_page_op_failed("delete", page_id, str(exc))
            return False
        # No application callbacks run in this ownership move. In the active
        # case the normal checked switch already stored the latest source
        # snapshot and moved its history away from the canvas exactly once.
        state = order[index]
        command.page = state.page
        command.dirty = state.dirty
        command.index = index
        command.prev_anchor_page_id = order[index - 1].page.id if index > 0 else None
        command.next_anchor_page_id = order[index + 1].page.id if index + 1 < len(order) else None
        command.was_active = was_active
        command.previous_active_page_id = previous_active
        command.successor_active_page_id = successor_id
        command.thumbnail_png = service._page_thumbnail_pngs.pop(page_id, None)
        command.history_bundle = service._page_history.take_bundle(page_id, is_active=False)
        del service._pages[index]
        service._reindex_pages()
        service._emit_document_changed("pages.delete")
        service._emit_pages_changed()
        return True
    finally:
        lease.release()
