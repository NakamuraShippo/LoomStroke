"""Temporary Delete-Undo structure and custody, committed after a checked switch."""

from __future__ import annotations

import logging
from typing import Any

from .page_command_completion import _current_page_completion
from .page_commands import DeletePageCommand
from .page_switch_request import _request_page_switch, _supports_checked_structure_switch
from .page_switch_transaction import _PageSwitchOutcome

logger = logging.getLogger(__name__)
_MISSING = object()


class _DeleteUndoReceipt:
    """Restore only this call's provisional page and moved command payload."""

    def __init__(self, service: Any, command: DeletePageCommand, completion: Any) -> None:
        self.service, self.command, self.completion = service, command, completion
        self.pages = service._pages
        self.state: Any | None = None
        self.bundle, self.thumbnail = command.history_bundle, command.thumbnail_png
        self.page_index = command.page.index
        self.coordinator = service._page_history
        page_id = command.page_id
        self.old_bundle = self.coordinator._bundles.get(page_id, _MISSING)
        self.old_ordinal = self.coordinator._ordinals.get(page_id, _MISSING)
        self.old_thumbnail = service._page_thumbnail_pngs.get(page_id, _MISSING)

    def rollback(self) -> None:
        command, service = self.command, self.service
        # These fields belong to the old command even after a new document
        # clears its book stack. Never transfer them to that new document.
        command.history_bundle, command.thumbnail_png = self.bundle, self.thumbnail
        command.page.index = self.page_index
        if not self.completion.document_is_current():
            return
        for index, state in enumerate(self.pages):
            if state is self.state:
                del self.pages[index]
                break
        page_id = command.page_id
        for mapping, original in (
            (self.coordinator._bundles, self.old_bundle),
            (self.coordinator._ordinals, self.old_ordinal),
            (service._page_thumbnail_pngs, self.old_thumbnail),
        ):
            if original is _MISSING:
                mapping.pop(page_id, None)
            else:
                mapping[page_id] = original
        # The coordinator's next ordinal remains monotonic. Only the exact
        # original entry age is restored; unrelated pages retain their ages.
        service._reindex_pages()


def _delete_undo_position(service: Any, command: DeletePageCommand) -> int:
    previous = (
        service._page_index(command.prev_anchor_page_id)
        if command.prev_anchor_page_id is not None
        else -1
    )
    following = (
        service._page_index(command.next_anchor_page_id)
        if command.next_anchor_page_id is not None
        else -1
    )
    target = previous + 1 if previous >= 0 else following if following >= 0 else int(command.index)
    return max(0, min(target, len(service._pages)))


def _restore_deleted_page_checked(  # noqa: PLR0911, PLR0912, PLR0915
    service: Any, command: DeletePageCommand
) -> bool | None:
    """Keep the previous bool/None public-hook distinction used by Delete."""
    from .page_service import _PageState  # noqa: PLC0415

    completion = _current_page_completion(service)
    if completion is None or not _supports_checked_structure_switch(service):
        return None
    page_id, page = command.page_id, command.page
    if page is None or service._find_page_state(page_id) is not None:
        error = "command was never applied" if page is None else "page already exists"
        service._emit_page_op_failed("delete_undo", page_id, error)
        return False
    receipt = _DeleteUndoReceipt(service, command, completion)
    lease = service._hold_page_residency()
    succeeded = False
    try:
        try:
            if not completion.activate():
                raise RuntimeError("page changed before delete undo")
            if receipt.bundle is not None and (
                receipt.bundle.page_id != page_id or receipt.old_bundle is not _MISSING
            ):
                raise RuntimeError("restored page history has another owner")
            if not completion.hold_capture():
                raise RuntimeError("page changed before temporary restoration")
            state = _PageState(page=page, dirty=command.dirty)
            receipt.state = completion.provisional_state = state
            service._pages.insert(_delete_undo_position(service, command), state)
            service._reindex_pages()
            if command.thumbnail_png:
                service._page_thumbnail_pngs[page_id] = command.thumbnail_png
                command.thumbnail_png = None
            if receipt.bundle is not None:
                if not service._page_history.restore_bundle(receipt.bundle):
                    raise RuntimeError("restored page history could not be returned")
                command.history_bundle = None
            if not completion.allows_commit():
                return False
            if command.was_active:
                outcome = _request_page_switch(
                    service,
                    page_id,
                    failure_context=("delete_undo", page_id),
                    require_recovery=True,
                    defer_settlement=True,
                )
                if outcome is not _PageSwitchOutcome.COMMITTED:
                    if (
                        outcome is _PageSwitchOutcome.SUPERSEDED
                        and completion.document_is_current()
                    ):
                        service._emit_page_op_failed(
                            "delete_undo", page_id, "page changed during delete undo"
                        )
                    return False
            if not completion.allows_commit() or service._find_page_state(page_id) is not state:
                return False
            service._emit_document_changed("pages.add")
            service._emit_pages_changed()
            succeeded = True
            return True
        except Exception as exc:
            logger.exception("Failed to restore deleted page %s", page_id)
            if completion.document_is_current():
                service._emit_page_op_failed("delete_undo", page_id, str(exc))
            return False
    finally:
        try:
            if not succeeded:
                try:
                    receipt.rollback()
                except Exception:
                    lease.preserve()
                    raise
                completion.confirm_rollback()
        finally:
            lease.release()
