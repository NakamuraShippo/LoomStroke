"""Runtime-only ownership of edits that cannot yet supply committed pixels."""

from __future__ import annotations

from typing import Any


class DocumentCaptureDeferred(RuntimeError):
    """An edit owner must restore or commit its temporary document mutation."""

    reason_code = "document_edit_pending"

    def __init__(self) -> None:
        super().__init__("Finish or cancel the current edit before saving.")


class _DocumentCaptureLease:
    def __init__(self, gate: _DocumentCaptureGate, token: object) -> None:
        self._gate = gate
        self._token = token

    @property
    def is_active(self) -> bool:
        return self._token in self._gate.tokens

    def release(self) -> None:
        # Old leases cannot release another edit, including after a document reset.
        self._gate.tokens.discard(self._token)


class _DocumentCaptureGate:
    def __init__(self) -> None:
        self.tokens: set[object] = set()

    def acquire(self) -> _DocumentCaptureLease:
        token = object()
        self.tokens.add(token)
        return _DocumentCaptureLease(self, token)


class DocumentCaptureGuardMixin:
    """Internal Canvas capability shared by the default and legacy canvases.

    Owners acquire before changing live content and release only after a
    successful restore or command commit. Leases are deliberately NOT
    released by garbage collection: losing an owner is not proof of recovery.
    """

    def _begin_transient_document_edit(self) -> _DocumentCaptureLease:
        gate = self.__dict__.get("_document_capture_gate_state")
        if gate is None:
            gate = _DocumentCaptureGate()
            self._document_capture_gate_state = gate
        return gate.acquire()

    def _document_capture_blocked(self) -> bool:
        gate = self.__dict__.get("_document_capture_gate_state")
        if gate is not None and gate.tokens:
            return True
        history = self.__dict__.get("_history_service")
        if history is None:
            return False
        # Observe only the existing default service and its current owner.
        # Looking up the guard must never create a history service, and an old
        # attempt's finally must not block a replacement document or page owner.
        from .canvas_history_adapter import CanvasHistoryService  # noqa: PLC0415
        from .canvas_history_restore_state import HistoryRestoreOwner  # noqa: PLC0415

        if type(history) is not CanvasHistoryService:
            return False
        owner = vars(history).get("_restore_owner")
        return (
            type(owner) is HistoryRestoreOwner and vars(owner).get("_capture_in_progress") is True
        )

    def _reset_document_capture_gate(self) -> None:
        """Forget old edits only when the document has actually been replaced."""
        gate = self.__dict__.get("_document_capture_gate_state")
        if gate is not None:
            gate.tokens.clear()


def is_document_capture_blocked(canvas: Any) -> bool:
    query = getattr(canvas, "_document_capture_blocked", None)
    return callable(query) and query() is True


def assert_document_capture_ready(canvas: Any) -> None:
    if is_document_capture_blocked(canvas):
        raise DocumentCaptureDeferred()


def notify_document_capture_committed(command: Any) -> None:
    """Release opt-in pre-applied edits after the full submit boundary succeeds."""
    callback = getattr(type(command), "_on_document_capture_committed", None)
    if callable(callback):
        callback(command)
