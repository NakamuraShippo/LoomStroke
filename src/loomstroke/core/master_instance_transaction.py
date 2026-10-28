"""Call-local failure recovery for an already prepared master instance command.

The checked master adapter finishes target hydration and active-edit capture
before creating this recovery boundary. Canvas
model recovery is supplied by a narrow, call-local factory; this module does not
own page-switch custody or repeat the canvas installer's field definitions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from enum import Enum, auto
from typing import Any, Protocol

from .document_capture_guard import is_document_capture_blocked
from .page_mutation_preflight import PageMutationPreflight

logger = logging.getLogger(__name__)


class _CanvasModelRecovery(Protocol):
    def restore(self) -> None:
        """Restore retained live model references without installing or notifying."""


class _MasterInstanceOutcome(Enum):
    COMMITTED = auto()
    REFUSED = auto()
    ROLLED_BACK = auto()
    NEW_DOCUMENT = auto()
    RECOVERY_PENDING = auto()


class _MasterPageRecovery:
    """Retain one original list, its layer references, and its canonical dirty bit."""

    def __init__(self, state: Any) -> None:
        self.state, self.page, self.dirty = state, state.page, state.dirty
        self.layers, self.contents = state.page.layers, tuple(state.page.layers)

    def restore(self) -> None:
        # The primitive can both replace the list during capture and mutate the
        # replacement in place. Neither the LayerSnapshot nor pixels are copied.
        self.layers[:] = self.contents
        # PageSnapshot and _PageState are slots dataclasses, so they have no
        # __dict__ state bundle. These fields have no callbacks or setters.
        object.__setattr__(self.page, "layers", self.layers)
        object.__setattr__(self.state, "dirty", self.dirty)


class _MasterInstanceTransaction:
    """Execute one direction and recover the failed step plus its successful prefix.

    Prepared steps preserve content-history ownership and defer external
    notifications through book completion. This transaction never executes an
    arbitrary page setter. An unexpected owner change/failed restore reports
    RECOVERY_PENDING without blindly releasing its token; it is not a product
    rescue policy or a long-lived registry. New documents receive no old rollback.
    """

    def __init__(
        self,
        service: Any,
        transitions: Sequence[Any],
        *,
        reverse: bool = False,
        canvas_recovery_factory: Callable[[Any], _CanvasModelRecovery],
        prepared_steps: Sequence[Any],
        completion: Any = None,
        final_guard: Callable[[], bool] | None = None,
    ) -> None:
        self._service, self._canvas = service, service._canvas
        self._transitions = tuple(reversed(transitions)) if reverse else tuple(transitions)
        self._scope = PageMutationPreflight(service, tuple(t.page_id for t in transitions))
        self._document = getattr(self._canvas, "_doc", None)
        self._page_set = service._pages
        self._active = service._active_page_id
        self._factory = canvas_recovery_factory
        self._prepared_steps = tuple(reversed(prepared_steps)) if reverse else tuple(prepared_steps)
        self._completion, self._final_guard = completion, final_guard
        self._lease: Any = None
        self._started = False
        self._journal: list[_MasterPageRecovery] = []
        self.outcome = _MasterInstanceOutcome.REFUSED

    def _allows_apply(self) -> bool:
        return (self._completion is None or self._completion.allows_commit()) and (
            self._final_guard is None or self._final_guard()
        )

    def _release_capture(self) -> None:
        if self._completion is not None:
            self._completion.confirm_rollback()
        elif self._lease is not None:
            self._lease.release()

    def _new_document(self) -> bool:
        return (
            self._service._canvas is not self._canvas
            or getattr(self._canvas, "_doc", None) is not self._document
            or self._service._pages is not self._page_set
        )

    def _capture_ready(self) -> bool:
        return (
            not is_document_capture_blocked(self._canvas)
            and not self._canvas._in_transaction
            and self._canvas._active_history_group is None
        )

    def _same_scope(self) -> bool:
        return (
            self._scope.is_ready()
            and self._service._active_page_id == self._active
            and self._service._book_history is self._book
            and all(page.state.page is page.page for page in self._pages.values())
            and self._book._trim_generation == self._book_generation
            and self._book._undo is self._book_undo
            and self._book._redo is self._book_redo
            and len(self._book_undo) == len(self._book_undo_entries)
            and len(self._book_redo) == len(self._book_redo_entries)
            and all(a is b for a, b in zip(self._book_undo, self._book_undo_entries, strict=True))
            and all(a is b for a, b in zip(self._book_redo, self._book_redo_entries, strict=True))
            and self._prepared_history_current()
        )

    def _prepared_history_current(self) -> bool:
        """Known prepared installs never change content-history ownership or groups.

        This stronger invariant decides whether rollback is still ours.
        Installer capability flags are deliberately not used here.
        """
        return (
            self._canvas.__dict__.get("_history_service") is self._history
            and self._history._restore_owner is self._content_owner
            and self._history._restore_generation == self._content_generation
            and self._canvas._undo_stack is self._content_stacks[0][0]
            and self._canvas._redo_stack is self._content_stacks[1][0]
            and all(
                len(stack) == len(groups)
                and all(a is b for a, b in zip(stack, groups, strict=True))
                for stack, groups in self._content_stacks
            )
        )

    def _prepare_recovery(self) -> None:
        # This existing plain-state primitive imports PageService, hence the
        # local import after its initialization rather than an eager cycle.
        from .image_document_state import ImageDocumentStateBundle  # noqa: PLC0415

        self._book = self._service._book_history
        self._book_generation = self._book._trim_generation
        self._book_undo, self._book_redo = self._book._undo, self._book._redo
        self._book_undo_entries = tuple(self._book_undo)
        self._book_redo_entries = tuple(self._book_redo)
        self._pages = {
            page_id: _MasterPageRecovery(state) for page_id, state in self._scope.targets
        }
        self._history = self._canvas._history()
        self._content_owner = self._history._restore_owner
        self._content_generation = self._history._restore_generation
        self._history_owner = ImageDocumentStateBundle.prepare(
            self._history, {"_restore_owner": None}
        )
        self._stack_fields = ImageDocumentStateBundle.prepare(
            self._canvas, {"_undo_stack": None, "_redo_stack": None}
        )
        # history_clear mutates the old deques in place. Retaining their
        # references alone loses the original groups; shallow tuples suffice.
        self._content_stacks = tuple(
            (stack, tuple(stack)) for stack in (self._canvas._undo_stack, self._canvas._redo_stack)
        )
        self._model = self._factory(self._canvas)

    def run(self) -> _MasterInstanceOutcome:
        if self._started:
            raise RuntimeError("a master instance transaction can run only once")
        self._started = True
        if not self._transitions or not self._scope.is_ready() or not self._capture_ready():
            return self.outcome
        try:
            self._prepare_recovery()
        except Exception:
            logger.exception("Failed to prepare master instance recovery")
            return self.outcome
        if (
            not self._same_scope()
            or not self._capture_ready()
            or not self._allows_apply()
            or not self._prepared_valid()
        ):
            return self.outcome
        return self._run_with_capture()

    def _prepared_valid(self) -> bool:
        return len(self._prepared_steps) == len(self._transitions) and all(
            step.page_id == transition.page_id
            for step, transition in zip(self._prepared_steps, self._transitions, strict=True)
        )

    def _run_with_capture(self) -> _MasterInstanceOutcome:
        if self._completion is None:
            self._lease = self._canvas._begin_transient_document_edit()
        else:
            held = self._completion.hold_capture()
            self._lease = self._completion._capture_lease
            if not held:
                return self._failed()
        # The global gate is now blocked by OUR token. Check ownership and
        # scope here, rather than refusing our own hold via _capture_ready.
        if not self._same_scope() or not self._allows_apply() or not self._lease.is_active:
            return self._failed()
        return self._apply_steps()

    def _apply_steps(self) -> _MasterInstanceOutcome:
        try:
            for index, transition in enumerate(self._transitions):
                if not self._same_scope() or not self._allows_apply():
                    return self._failed()
                # Register BEFORE calling: False and exceptions may follow a
                # partial mutation of the failing page itself.
                self._journal.append(self._pages[transition.page_id])
                succeeded = self._prepared_steps[index].install()
                if not succeeded or not self._same_scope() or not self._allows_apply():
                    return self._failed()
        except Exception:
            logger.exception("Master instance step failed")
            return self._failed()
        if self._completion is None:
            self._lease.release()
        # Checked book execution retains this same short-lived token until
        # completion.seal(), after the normal stack append/move and trim.
        self.outcome = _MasterInstanceOutcome.COMMITTED
        return self.outcome

    def _failed(self) -> _MasterInstanceOutcome:
        if self._new_document() or not self._journal:
            # An old token cannot release the replacement document's preview.
            self._release_capture()
            self.outcome = (
                _MasterInstanceOutcome.NEW_DOCUMENT
                if self._new_document()
                else _MasterInstanceOutcome.REFUSED
            )
            return self.outcome
        if not self._same_scope():
            # Restoring the old canvas here would erase the new active page's
            # legitimate edits, while releasing would permit a partial save.
            self.outcome = _MasterInstanceOutcome.RECOVERY_PENDING
            return self.outcome
        try:
            generation = self._history._restore_generation
            restored: set[int] = set()
            for page in reversed(self._journal):
                if id(page) not in restored:
                    page.restore()
                    restored.add(id(page))
            self._model.restore()
            for stack, contents in self._content_stacks:
                stack.clear()
                stack.extend(contents)
            self._stack_fields.restore()
            self._history_owner.restore()
            # A published ownership generation is a fence, not undoable data.
            self._history._restore_generation = (
                max(generation, self._history._restore_generation) + 1
            )
        except Exception:
            logger.exception("Master instance recovery remains incomplete")
            self.outcome = _MasterInstanceOutcome.RECOVERY_PENDING
            return self.outcome
        self._release_capture()
        self.outcome = _MasterInstanceOutcome.ROLLED_BACK
        return self.outcome
