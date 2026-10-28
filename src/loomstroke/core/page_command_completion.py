"""Call-local completion delivery for checked page structure commands.

Only the known Delete/Master helpers may opt in. Public command return values
stay boolean, and unknown command implementations keep their immediate events.
The context delays semantic helpers, including their derived geometry updates,
until model changes and the existing book stack have both settled.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .canvas_history_clear_continuation import _HistoryClearContinuation
from .page_commands import ApplyMasterInstancesCommand, DeletePageCommand

logger = logging.getLogger(__name__)

_active: ContextVar[weakref.ReferenceType[_PageCommandCompletion] | None] = ContextVar(
    "page_command_completion", default=None
)


class _PageCommandCompletion:
    """One command's optional redraw lease and deferred completion helpers."""

    def __init__(self, book: Any, command: Any, operation: str) -> None:
        self.book, self.service = book, book._service
        self.eligible = type(command) in (DeletePageCommand, ApplyMasterInstancesCommand)
        self.canvas = getattr(self.service, "_canvas", None)
        self.document = getattr(self.canvas, "_doc", None)
        self.pages = getattr(self.service, "_pages", None)
        self.generation = book._trim_generation
        self.enabled = self.flushing = self.closed = False
        self._attempt_finished = False
        self._succeeded = self._nested_book_committed = False
        self._queue: list[tuple[str, tuple[Any, ...]]] = []
        self._failures: list[tuple[str, tuple[Any, ...]]] = []
        self._refusals: list[tuple[str, tuple[Any, ...]]] = []
        self._settled: tuple[Any, ...] | None = None
        self._baseline = 0
        self._capture_lease: Any | None = None
        self.provisional_state: Any | None = None
        self._parent = _active.get()
        self._operation = operation
        self._candidate = (
            book.peek_undo_entry()
            if operation == "undo"
            else book.peek_redo_entry() if operation == "redo" else None
        )

    def document_is_current(self) -> bool:
        return (
            getattr(self.service, "_canvas", None) is self.canvas
            and getattr(self.canvas, "_doc", None) is self.document
            and getattr(self.service, "_pages", None) is self.pages
            and self.book._trim_generation == self.generation
        )

    def activate(self) -> bool:
        """Opt a checked helper into completion delivery before any mutation."""
        if not self.eligible or self.closed or not self.document_is_current():
            return False
        before = self._capture_settlement()
        if not self.enabled:
            self._baseline = self.canvas._defer_recompose_depth
            self.enabled = True
            self.canvas.defer_recompose()
        return self.allows_commit() and self._model_matches(before)

    def allows_commit(self) -> bool:
        """An unrelated nested book commit must keep its own stack position."""
        if not self.enabled:
            return True
        candidate_current = True
        if self._operation == "undo":
            candidate_current = self.book.peek_undo_entry() is self._candidate
        elif self._operation == "redo":
            candidate_current = self.book.peek_redo_entry() is self._candidate
        return self.document_is_current() and not self._nested_book_committed and candidate_current

    def seal(self) -> None:
        """Capture the actual stack after its normal trim, before batch exit."""
        self._succeeded = True
        self._settled = self._capture_settlement()
        self._release_capture()
        for parent in self._parents():
            if parent.book is not self.book:
                continue
            # A short-lived flag also detects undo/redo away and back to the
            # same endpoints. No persistent history counter is required.
            parent._nested_book_committed = True

    def hold_capture(self) -> bool:
        """Protect provisional model state with the existing document edit gate."""
        if self._capture_lease is None:
            self._capture_lease = self.canvas._begin_transient_document_edit()
        return self.allows_commit() and self._capture_lease.is_active

    def confirm_rollback(self) -> None:
        """Called only after the owning receipt has restored its temporary model."""
        self._release_capture()

    def _release_capture(self) -> None:
        if self._capture_lease is not None:
            self._capture_lease.release()
            self._capture_lease = None

    @contextmanager
    def attempt(self) -> Iterator[None]:
        """End model ownership before the notifier batch calls application code."""
        try:
            yield
        finally:
            self._attempt_finished = True

    def _parents(self) -> Iterator[_PageCommandCompletion]:
        reference = self._parent
        while reference is not None:
            parent = reference()
            if parent is None:
                return
            yield parent
            reference = parent._parent

    def _capture_settlement(self) -> tuple[Any, ...]:
        return (
            getattr(self.service, "_active_page_id", None),
            tuple(self.pages or ()),
        )

    def is_current(self) -> bool:
        if not self.document_is_current() or self._nested_book_committed or self._settled is None:
            return False
        return self._model_matches(self._settled)

    def _model_matches(self, settled: tuple[Any, ...]) -> bool:
        active, order = settled
        current = self.pages or ()
        # New content may clear book redo (H6), and memory trims may remove
        # old entries. Neither invalidates the committed page structure.
        # Book commits, including undo/redo away and back to the same model,
        # are detected by the short-lived parent-chain flag above.
        return (
            getattr(self.service, "_active_page_id", None) == active
            and len(current) == len(order)
            and all(before is after for before, after in zip(order, current, strict=True))
        )

    def stage(self, method: str, args: tuple[Any, ...], *, failure: bool) -> bool:
        if (
            not self.enabled
            or self.flushing
            or self._succeeded
            or self._attempt_finished
            or not self.document_is_current()
        ):
            return False
        (self._failures if failure else self._queue).append((method, args))
        return True

    def finish(self) -> None:
        try:
            self._attempt_finished = True
            if not self.enabled:
                return
            self._resume_owned_level()
            self.flushing = True
            if not self._succeeded:
                self._settled = self._capture_settlement()
            if self._succeeded:
                for method, args in self._queue:
                    if not self.is_current():
                        break
                    self._deliver(method, args)
            else:
                # Failed model helpers discard success notifications. A
                # refusal still belongs to the original document after an
                # independently completed same-document preparation edit.
                for method, args in self._failures:
                    if not self.is_current():
                        break
                    self._deliver(method, args)
            for method, args in self._refusals:
                if not self.is_current():
                    break
                self._deliver(method, args)
        finally:
            self.closed = True
            self.flushing = False
            self._queue.clear()
            self._failures.clear()
            self._refusals.clear()
            self._settled = None
            self.provisional_state = None

    def _deliver(self, method: str, args: tuple[Any, ...]) -> None:
        try:
            getattr(self.service, method)(*args)
        except Exception:
            # Model and stacks have committed; a UI/derived notification is
            # outside the rollback boundary and must never pop a new entry.
            logger.exception("Page command completion helper %s failed", method)

    def _resume_owned_level(self) -> None:
        canvas = self.canvas
        if getattr(canvas, "_doc", None) is not self.document:
            return
        depth = canvas._defer_recompose_depth
        if depth <= self._baseline:
            return
        context = _HistoryClearContinuation(canvas)
        pages = getattr(self.service, "_pages", None)
        active = getattr(self.service, "_active_page_id", None)
        clock = getattr(self.service, "_history_sequence", None)
        sequence = getattr(clock, "_next", None)
        try:
            canvas.resume_recompose()
        except Exception:
            if (
                context.is_current()
                and getattr(self.service, "_pages", None) is pages
                and getattr(self.service, "_active_page_id", None) == active
                and getattr(self.service, "_history_sequence", None) is clock
                and getattr(clock, "_next", None) == sequence
                and canvas._defer_recompose_depth == depth
            ):
                canvas._defer_recompose_depth -= 1
            logger.exception("Page command redraw resume failed after model settlement")


@contextmanager
def _page_command_completion(
    book: Any, command: Any, operation: str = "execute"
) -> Iterator[_PageCommandCompletion]:
    completion = _PageCommandCompletion(book, command, operation)
    token = _active.set(weakref.ref(completion))
    try:
        yield completion
    finally:
        try:
            completion.finish()
        finally:
            _active.reset(token)


def _current_page_completion(service: Any) -> _PageCommandCompletion | None:
    reference = _active.get()
    completion = reference() if reference is not None else None
    if (
        completion is not None
        and completion.service is service
        and completion.eligible
        and not completion.closed
        and completion.document_is_current()
    ):
        return completion
    return None


def _refuse_nested_page_command(book: Any, command: Any, operation: str) -> bool:
    """Keep an uncommitted checked structure edit indivisible on its document."""
    reference = _active.get()
    completion = reference() if reference is not None else None
    if completion is None:
        return False
    candidates = (completion, *completion._parents())
    completion = next(
        (
            candidate
            for candidate in candidates
            if candidate.book is book
            and not candidate.closed
            and not candidate._attempt_finished
            and candidate.enabled
            and not candidate._succeeded
            and candidate.document_is_current()
        ),
        None,
    )
    if completion is None:
        return False
    ids = getattr(command, "affected_page_ids", ())
    page_id = ids[0] if ids else getattr(completion.service, "_active_page_id", "")
    label = getattr(command, "label_key", "")
    op = label.removeprefix("page.") if label.startswith("page.") else operation
    if operation != "execute":
        op += f"_{operation}"
    completion._refusals.append(
        (
            "_emit_page_op_failed",
            (op, page_id, "another page structure operation is still being prepared"),
        )
    )
    return True


def _refuse_provisional_page_navigation(service: Any, page_id: str) -> bool:
    """A half-restored page is selectable only by its own private switch."""
    reference = _active.get()
    completion = reference() if reference is not None else None
    if completion is None:
        return False
    for candidate in (completion, *completion._parents()):
        state = candidate.provisional_state
        if (
            state is not None
            and candidate.service is service
            and candidate.enabled
            and not candidate._succeeded
            and not candidate._attempt_finished
            and candidate.document_is_current()
            and service._find_page_state(page_id) is state
        ):
            candidate._refusals.append(
                (
                    "_emit_page_op_failed",
                    ("set_active", page_id, "page restoration is still being prepared"),
                )
            )
            return True
    return False


@contextmanager
def _public_page_switch_completion() -> Iterator[None]:
    """Independent public navigation must not join an older book edit's queue.

    Keep the attempt owner visible: its precommit nested-book refusal still
    applies inside the independently requested navigation's callbacks.
    """
    reference = _active.get()
    parent = reference() if reference is not None else None
    if parent is None:
        yield
        return
    with _page_command_completion(parent.book, None, "navigation"):
        yield


def _stage_page_completion(service: Any, method: str, *args: Any, failure: bool = False) -> bool:
    completion = _current_page_completion(service)
    return completion is not None and completion.stage(method, args, failure=failure)


def _allows_page_command_commit(service: Any) -> bool:
    completion = _current_page_completion(service)
    return completion is None or completion.allows_commit()


def _capture_page_completion_delivery(service: Any) -> _PageCommandCompletion | None:
    """Capture before a semantic helper calls application code; recheck after it."""
    completion = _current_page_completion(service)
    if completion is not None and completion.flushing and completion.is_current():
        return completion
    return None
