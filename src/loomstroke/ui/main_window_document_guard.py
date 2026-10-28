"""Authorize one UI document replacement without losing unsaved work."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

_Result = TypeVar("_Result")


def guard_document_replacement(
    denied_result: _Result,
) -> Callable[[Callable[..., _Result]], Callable[..., _Result]]:
    """Keep the gate occupied through the destructive operation and its events."""

    def decorate(method: Callable[..., _Result]) -> Callable[..., _Result]:
        @wraps(method)
        def guarded(coordinator: Any, *args: Any, **kwargs: Any) -> _Result:
            return coordinator._replacement_guard.run(
                lambda: method(coordinator, *args, **kwargs), denied_result
            )

        return guarded

    return decorate


class DocumentReplacementGuard:
    """Own UI authorization and event generations; never change document data."""

    def __init__(self, workflow: Any, window: Any, message_box: Callable[[], Any]) -> None:
        self._workflow = workflow
        self._window = window
        self._message_box = message_box
        self._active = False
        self._epoch = 0
        self._revision = 0
        self._saved_state: tuple[int, int, int, int] | None = None

    def document_changed(self) -> None:
        self._revision += 1

    def document_replaced(self) -> None:
        self._epoch += 1
        self._revision = 0
        self._saved_state = None

    def capture_state(self) -> tuple[int, int, int, int]:
        window = self._window
        return (
            self._epoch,
            self._revision,
            id(self._service()),
            id(getattr(getattr(window, "host", None), "canvas", None)),
        )

    def note_save_outcome(self) -> None:
        self._saved_state = self.capture_state()

    def run(self, operation: Callable[[], _Result], denied_result: _Result) -> _Result:
        if self._active or self._save_in_flight():
            return denied_result
        self._active = True
        try:
            if not self._confirm():
                return denied_result
            return operation()
        finally:
            self._active = False

    def _save_in_flight(self) -> bool:
        window = self._window
        service = self._service()
        return (
            getattr(window, "_save_async_in_flight", False) is True
            or getattr(window, "_closing_in_progress", False) is True
            or getattr(service, "_save_in_progress", False) is True
        )

    def _capture_blocked(self) -> bool:
        service = self._service()
        query = getattr(service, "_document_capture_blocked", None)
        return callable(query) and query() is True

    def _service(self) -> Any:
        getter = getattr(self._window, "_get_document_service", None)
        return getter() if callable(getter) else None

    def _confirm(self) -> bool:
        window = self._window
        # MCP new/close already passed its own discard/confirmation contract.
        # Busy saves were checked first, including an MCP async save.
        if getattr(window, "_mcp_document_operation_in_flight", False) is True:
            return True
        if not getattr(window, "_has_document", True):
            return True
        if not getattr(window, "_document_dirty", False) and not self._capture_blocked():
            return True
        return self._confirm_dirty_document()

    def _confirm_dirty_document(self) -> bool:
        window = self._window
        original = self.capture_state()
        capture_pending = self._capture_blocked()
        box = self._message_box()
        buttons = box.StandardButton
        reply = box.question(
            window,
            window._ts("Unsaved Changes"),
            window._ts("You have unsaved changes. Do you want to save before closing?"),
            buttons.Save | buttons.Discard | buttons.Cancel,
            buttons.Save,
        )
        # A modal dialog runs a nested event loop. Its answer only belongs to
        # the document content the user was asked about.
        if (
            self._save_in_flight()
            or self.capture_state() != original
            or self._capture_blocked() != capture_pending
        ):
            return False
        if reply == buttons.Discard:
            return True
        if reply != buttons.Save:
            return False

        target = window._document_path or window._prompt_save_path()
        if (
            target is None
            or self._save_in_flight()
            or self.capture_state() != original
            or self._capture_blocked() != capture_pending
        ):
            return False
        return self._save_and_confirm(target, original)

    def _save_and_confirm(self, target: Any, original: tuple[int, int, int, int]) -> bool:
        window = self._window
        saved = window._perform_save(target)
        current = self.capture_state()
        same_document = original[0] == current[0] and original[2:] == current[2:]
        if not saved or not same_document or self._save_in_flight():
            return False
        if not self._workflow._close_save_outcome_allows_close():
            return False
        if self._workflow._last_manual_save_outcome is None:
            # Event-less custom services retain the existing bool fallback,
            # provided no edit/document switch occurred inside that save.
            if current != original:
                return False
        elif self._saved_state != current:
            return False
        return not window._document_dirty and not self._capture_blocked()
