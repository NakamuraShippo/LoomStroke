"""Finalize a vector edit before it can be seen or saved as document content."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import nullcontext
from typing import TYPE_CHECKING

from .canvas_command_commit_guard import CanvasCommandCommitGuard
from .canvas_vector_apply_recovery import VectorApplyRecovery
from .comic_panels.atomic_scope import panel_atomic_scope
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent
from .layer_command_commit_context import _CommandHistoryReceipt
from .vector_initial_edit_policy import allows_initial_vector_edit

if TYPE_CHECKING:
    from .canvas_vector_command_submit_service import (
        CanvasVectorCommandSubmitService,
        _VectorCommand,
    )

logger = logging.getLogger(__name__)


def submit_vector_with_finalizer(
    service: CanvasVectorCommandSubmitService, command: _VectorCommand, finalize: Callable[[], None]
) -> bool:
    canvas = service._owner
    if (
        canvas._in_transaction
        or getattr(canvas, "_panel_edit_events", None) is not None
        or is_document_capture_blocked(canvas)
    ):
        return False
    guard = CanvasCommandCommitGuard(canvas)
    recovery = VectorApplyRecovery.capture(canvas)
    if recovery is None:
        return False
    receipt = _CommandHistoryReceipt(canvas, recovery.scope)
    # A content-history push can also invalidate redo on other pages and in the book.
    notifier = getattr(recovery.scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    pending_history = notifier._dirty if notifier is not None else False
    capture = canvas._begin_transient_document_edit()
    previous_callback = getattr(canvas, "_history_changed_callback", None)

    def hold_history_notification() -> None:
        pass

    try:
        with batch, panel_atomic_scope(canvas) as scope:
            canvas._history_changed_callback = hold_history_notification
            try:
                if not allows_initial_vector_edit(canvas, command) or not command.apply(canvas):
                    recovery.recover(guard)
                    return False
                service._record(command)
                if not receipt.recorded(command):
                    raise RuntimeError("Vector edit history was not recorded")
                finalize()
            except Exception:
                if recovery.scope.current():
                    guard.rollback(recovery)
                raise
            finally:
                if canvas._history_changed_callback is hold_history_notification:
                    canvas._history_changed_callback = previous_callback
                if notifier is not None:
                    notifier._dirty = pending_history
            scope.committed = True
            canvas._invalidate_layer_cache()
            canvas._recompose()
            capture.release()
        try:
            canvas._emit_history_changed()
            if canvas._event_bus is not None:
                canvas._event_bus.emit(
                    service._doc_changed_event,
                    DocumentChangeEvent(change_type="vector_command", source="canvas"),
                )
        except Exception:
            logger.exception("Finalized vector edit notification failed")
        return True
    finally:
        capture.release()
