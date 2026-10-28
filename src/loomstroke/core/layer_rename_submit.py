"""Commit a rename's history before publishing its document notification."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Any

from .commands_layer_lookup import find_layer
from .commands_layer_rename import RenameLayerCommand, _is_standard_rename_command
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext

logger = logging.getLogger(__name__)


class _RenameScope(_LayerCommandContext):
    """The rename owner keeps its notification meaning outside shared custody."""

    def __init__(self, canvas: Any, layer: Any = None) -> None:
        super().__init__(canvas, (layer,) if layer is not None else ())
        self.layer = layer

    def emit_document_change(self) -> None:
        if self.current():
            self.canvas._emit_doc_changed(
                DocumentChangeEvent(
                    change_type="layer_rename",
                    layer_id=self.layer.id if self.layer is not None else None,
                    source="canvas",
                )
            )


class _RenameReceipt:
    """Keep the name locally and delegate only the shared history references."""

    def __init__(self, canvas: Any, command: RenameLayerCommand, layer: Any) -> None:
        self.canvas, self.command, self.layer = canvas, command, layer
        self.scope = _RenameScope(canvas, layer)
        self.pages = self.scope.pages
        self.name = layer.name
        self.command_state = command._old, command._executed
        self.history = _CommandHistoryReceipt(canvas, self.scope)

    def recorded(self) -> bool:
        return self.history.recorded(self.command)

    def can_rollback(self) -> bool:
        return self.layer.name == self.command._new and self.history.can_rollback(self.command)

    def restore(self) -> None:
        self.layer.name = self.name
        self.command._old, self.command._executed = self.command_state
        self.history.restore()

    def rollback(self) -> bool:
        if not self.can_rollback():
            return False
        self.restore()
        return True


def _submit_rename_command(  # noqa: PLR0911 - preserve explicit pre-mutation and owner boundary refusals
    canvas: Any, command: RenameLayerCommand, label: str
) -> bool:
    layer = find_layer(canvas, command.layer_id)
    if layer is None or not command._new or is_document_capture_blocked(canvas):
        return False
    receipt = _RenameReceipt(canvas, command, layer)
    if not receipt.scope.current():
        return False
    notifier = getattr(receipt.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    capture = canvas._begin_transient_document_edit()
    previous = command._defer_notifications
    command._defer_notifications = True
    committed, recovered = False, False
    try:
        with batch:
            try:
                if not command.apply(canvas):
                    # The qualified default implementation returns False only
                    # before changing the name or command execution state.
                    recovered = True
                    return False
                if not receipt.scope.current():
                    return False
                canvas._record_history_command(label, command)
                if not receipt.recorded():
                    recovered = receipt.rollback()
                    return False
            except Exception:
                recovered = receipt.rollback()
                logger.exception("Layer rename failed before history commitment")
                return False
            committed = True
            # Name and history are committed. No observer or rendering error
            # below is allowed to trigger the old receipt's rollback.
            try:
                if command._notify_document_on_submit:
                    receipt.scope.emit_document_change()
                capture.release()
                if receipt.scope.current():
                    command._defer_notifications = previous
                    command._notify(canvas, layer)
                if receipt.scope.current():
                    canvas._recompose()
            except Exception:
                logger.exception("Layer rename notification failed after commitment")
        return True
    finally:
        command._defer_notifications = previous
        if committed or recovered or not receipt.scope.current():
            capture.release()


def _capture_rename_restore_notice(canvas: Any, group: Any) -> _RenameScope | None:
    """Only successful groups containing a rename need this document notice."""
    if group is not None and any(
        _is_standard_rename_command(getattr(entry, "command", None)) for entry in group.entries
    ):
        return _RenameScope(canvas)
    return None
