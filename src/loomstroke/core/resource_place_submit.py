"""Commit one prepared Place command before delivering its model notifications."""

from __future__ import annotations

import logging
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

from .canvas_command_commit_guard import CanvasCommandCommitGuard
from .commands_layer_lookup import find_layer
from .events import DocumentChangeEvent

logger = logging.getLogger(__name__)


def _same_items(left, right):
    return len(left) == len(right) and all(a is b for a, b in zip(left, right, strict=True))


class _PlaceFence:
    """A call-local fence around this command's exact tree/history custody."""

    def __init__(self, canvas, command):
        self.canvas, self.command = canvas, command
        self.guard = CanvasCommandCommitGuard(canvas)
        self.document, self.roots = canvas._doc, canvas._layers
        self.history = canvas._history()
        self.generation = self.history._restore_generation
        self.pages = self.guard.pages
        self.page_set = getattr(self.pages, "_pages", None)
        self.active_page = getattr(self.pages, "_active_page_id", None)
        self.active_state = self.pages._find_page_state(self.active_page) if self.pages else None
        self.book_generation = getattr(self.guard.book, "_trim_generation", None)
        self.in_transaction = canvas._in_transaction

    def current(self):
        canvas = self.canvas
        return (
            canvas._doc is self.document
            and canvas._layers is self.roots
            and self.history._restore_owner is self.guard.restore_owner
            and self.history._restore_generation == self.generation
            and canvas._undo_stack is self.guard.undo[0]
            and canvas._redo_stack is self.guard.redo[0]
            and getattr(self.pages, "_pages", None) is self.page_set
            and getattr(self.pages, "_active_page_id", None) == self.active_page
            and (
                self.pages is None
                or self.pages._find_page_state(self.active_page) is self.active_state
            )
            and getattr(self.guard.book, "_trim_generation", None) == self.book_generation
            and canvas._in_transaction == self.in_transaction
        )

    def owns_layer(self):
        return find_layer(self.canvas, self.command.layer_id) is self.command._created.layer

    def recorded(self):
        if not self.current() or not self.owns_layer():
            return False
        canvas, guard = self.canvas, self.guard
        if canvas._in_transaction:
            group = canvas._active_history_group
            return (
                group is not None
                and (guard.group is None or group is guard.group)
                and _same_items(canvas._undo_stack, guard.undo[1])
                and _same_items(canvas._redo_stack, guard.redo[1])
                and len(group.entries) == len(guard.group_entries) + 1
                and _same_items(group.entries[:-1], guard.group_entries)
                and getattr(group.entries[-1], "command", None) is self.command
            )
        current = tuple(canvas._undo_stack)
        if not current:
            return False
        group = current[-1]
        expected = (*guard.undo[1], group)
        limit = getattr(canvas._undo_stack, "maxlen", None)
        if limit is not None:
            expected = expected[-limit:]
        return (
            _same_items(current, expected)
            and len(group.entries) == 1
            and getattr(group.entries[0], "command", None) is self.command
            and not canvas._redo_stack
        )

    def _redo_context_current(self):
        guard = self.guard
        if guard.book is not None and (
            guard.book._undo is not guard.book_undo[0]
            or not _same_items(guard.book._undo, guard.book_undo[1])
            or guard.book._redo is not guard.book_redo[0]
            or (guard.book._redo and not _same_items(guard.book._redo, guard.book_redo[1]))
        ):
            return False
        if guard.coordinator is not None and (
            any(
                guard.bundles.get(key) is not bundle
                for key, bundle in guard.coordinator._bundles.items()
            )
            or any(
                guard.ordinals.get(key) != value
                for key, value in guard.coordinator._ordinals.items()
            )
        ):
            return False
        return all(
            bundle.undo_groups is undo
            and _same_items(undo, undo_items)
            and bundle.redo_groups is redo
            and (not redo or _same_items(redo, redo_items))
            for bundle, undo, undo_items, redo, redo_items in guard.bundle_stacks
        )

    def can_rollback(self):
        others = [layer for layer in self.roots if layer is not self.command._created.layer]
        if (
            not self.current()
            or not _same_items(others, self.guard.roots)
            or not self._redo_context_current()
        ):
            return False
        group = self.canvas._active_history_group
        unchanged = (
            _same_items(self.canvas._undo_stack, self.guard.undo[1])
            and _same_items(self.canvas._redo_stack, self.guard.redo[1])
            and group is self.guard.group
            and (group is None or _same_items(group.entries, self.guard.group_entries))
        )
        return unchanged or self.recorded()

    def rollback_if_owned(self):
        if not self.can_rollback():
            return False
        try:
            self.guard.rollback(SimpleNamespace(revert=self.command._rollback))
        except Exception:
            logger.exception("Placed image model recovery failed")
            return False
        return True


class _PlaceNotifications:
    """Hold only this call's existing layer queue and recompose level."""

    def __init__(self, fence):
        self.fence, self.canvas = fence, fence.canvas
        self.previous = getattr(self.canvas, "_panel_edit_events", None)
        self.pending = []
        self.document = self.canvas._doc
        self.baseline = self.canvas._defer_recompose_depth
        self.previous_history = getattr(self.canvas, "_history_changed_callback", None)
        self._history_handler = self._stage_history
        self.history_pending = False
        self.returned_level = False
        self.recompose_pending = False

    def _stage_history(self):
        self.history_pending = True

    def begin(self):
        self.canvas._panel_edit_events = self.pending
        self.canvas._history_changed_callback = self._history_handler
        self.canvas.defer_recompose()

    @contextmanager
    def hold(self):
        try:
            self.begin()
            yield
        finally:
            self.return_ownership()

    def _same_document(self):
        return (
            self.canvas._doc is self.document
            and getattr(self.fence.pages, "_pages", None) is self.fence.page_set
        )

    def return_ownership(self):
        """Return our counter without the presentation half of resume.

        The standard resume first decrements this counter, then recomposes.
        Separating those steps lets DOC_CHANGED settle the revision before any
        redraw observer, without carrying our counter into a new document.
        """
        canvas = self.canvas
        if (
            not self.returned_level
            and canvas._doc is self.document
            and canvas._defer_recompose_depth > self.baseline
        ):
            canvas._defer_recompose_depth -= 1
            self.returned_level = True
            if canvas._defer_recompose_depth == 0:
                self.recompose_pending = canvas._defer_recompose_needed
                canvas._defer_recompose_needed = False
        if getattr(canvas, "_panel_edit_events", None) is self.pending:
            canvas._panel_edit_events = self.previous if self._same_document() else None
        if getattr(canvas, "_history_changed_callback", None) is self._history_handler:
            canvas._history_changed_callback = self.previous_history

    def _flush_recompose(self):
        canvas = self.canvas
        if (
            self.returned_level
            and canvas._doc is self.document
            and canvas._defer_recompose_depth == 0
            and (self.recompose_pending or canvas._defer_recompose_needed)
        ):
            canvas._defer_recompose_needed = False
            try:
                canvas._recompose()
            except Exception:
                logger.exception("Placed image redraw notification failed")

    def finish(self, committed):
        canvas, fence = self.canvas, self.fence
        if self.history_pending and self._same_document():
            # Only the current model is inspected by the original callback;
            # no cached old history payload crosses a document replacement.
            try:
                canvas._emit_history_changed()
            except Exception:
                logger.exception("Placed image history notification failed")
        self._flush_recompose()
        if not committed or not fence.current() or not fence.owns_layer():
            return
        if self.previous is not None:
            self.previous.extend(self.pending)
        else:
            for event_name, kwargs in self.pending:
                if not fence.current() or not fence.owns_layer():
                    return
                try:
                    canvas._publish_layer_event(event_name, **kwargs)
                except Exception:
                    logger.exception("Placed image layer notification failed")


def submit_resource_place(canvas, command):
    """Keep model/stack rollback separate from postcommit application callbacks."""
    if any(layer.native_cmyk is not None for layer in canvas._layer_tree().flatten_layers()):
        return False
    fence = _PlaceFence(canvas, command)
    notifications = _PlaceNotifications(fence)
    notifier = getattr(fence.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    committed = False
    recovered = False
    started = False
    capture = canvas._begin_transient_document_edit()
    command._submitting = True
    try:
        with batch, notifications.hold():
            try:
                if fence.current() and command._created.layer is None:
                    command._prepare(canvas)
                if not fence.current():
                    return False
                started = True
                if not command.apply(canvas) or not fence.current() or not fence.owns_layer():
                    recovered = fence.rollback_if_owned()
                    if recovered:
                        capture.release()
                    return False
                canvas._record_history_command("Place Image", command)
                if not fence.recorded():
                    recovered = fence.rollback_if_owned()
                    if recovered:
                        capture.release()
                    return False
                committed = True
            except Exception:
                recovered = fence.rollback_if_owned()
                if recovered:
                    capture.release()
                logger.exception("Prepared image placement failed")
                return False
            if committed and fence.current() and fence.owns_layer():
                notifications.return_ownership()
                # Keep capture protected during DOC_CHANGED too: a nested
                # synchronous save cannot finalize a pre-revision snapshot.
                canvas._emit_doc_changed(DocumentChangeEvent(change_type="resource.place"))
                capture.release()
    finally:
        command._submitting = False
        if not started or committed or recovered or not fence.current():
            capture.release()
        # No postcommit callback belongs to the old model rollback boundary.
        notifications.finish(committed)
    return committed
