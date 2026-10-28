"""Runtime ownership and history references shared by rename and resource Embed."""

from __future__ import annotations

from types import MethodType
from typing import Any

from .canvas_command_commit_guard import CanvasCommandCommitGuard
from .canvas_history_clear_continuation import _HistoryClearContinuation, _stored_state
from .commands_layer_lookup import find_layer
from .page_mutation_preflight import PageMutationPreflight


def _same_items(left: Any, right: Any) -> bool:
    return len(left) == len(right) and all(a is b for a, b in zip(left, right, strict=True))


class _LayerCommandContext:
    """Keep one synchronous edit on its original document, page, and objects."""

    def __init__(self, canvas: Any, layers: tuple[Any, ...] = ()) -> None:
        self.canvas, self.layers = canvas, layers
        self.context = _HistoryClearContinuation(canvas)
        callback = _stored_state(canvas).get("_history_new_operation_callback")
        self.pages = callback.__self__ if isinstance(callback, MethodType) else None
        state = _stored_state(self.pages)
        self.page_set = state.get("_pages")
        self.active_page = state.get("_active_page_id")
        self.book = state.get("_book_history")
        self.book_generation = _stored_state(self.book).get("_trim_generation")
        self.page_preflight = (
            PageMutationPreflight(self.pages, (self.active_page,))
            if state.get("_canvas") is canvas
            and self.page_set is not None
            and self.active_page is not None
            else None
        )

    def current(self) -> bool:
        state = _stored_state(self.pages)
        return (
            self.context.is_current()
            and state.get("_pages") is self.page_set
            and state.get("_active_page_id") == self.active_page
            and _stored_state(self.book).get("_trim_generation") == self.book_generation
            and (self.page_preflight is None or self.page_preflight.is_current())
            and all(find_layer(self.canvas, layer.id) is layer for layer in self.layers)
        )


class _CommandHistoryReceipt:
    """Preserve only stacks and redo custody, never trees or pixel payloads."""

    def __init__(self, canvas: Any, scope: _LayerCommandContext) -> None:
        self.canvas, self.scope = canvas, scope
        self.undo, self.redo = canvas._undo_stack, canvas._redo_stack
        self.undo_items, self.redo_items = list(self.undo), list(self.redo)
        self.group = canvas._active_history_group
        self.group_items = tuple(self.group.entries) if self.group is not None else ()
        self.book = getattr(scope.pages, "_book_history", None)
        self.pool = getattr(scope.pages, "_page_history", None)
        self.book_redo = (
            (self.book._redo, list(self.book._redo)) if self.book is not None else ([], [])
        )
        self.book_undo = (
            (self.book._undo, list(self.book._undo)) if self.book is not None else ([], [])
        )
        self.bundles = dict(self.pool._bundles) if self.pool is not None else {}
        self.ordinals = dict(self.pool._ordinals) if self.pool is not None else {}
        self.inactive_redo = [
            (
                bundle,
                bundle.redo_groups,
                list(bundle.redo_groups),
                bundle.undo_groups,
                list(bundle.undo_groups),
            )
            for bundle in self.bundles.values()
        ]

    def recorded(self, command: Any) -> bool:
        if not self.scope.current():
            return False
        if self.canvas._in_transaction:
            group = self.canvas._active_history_group
            if group is None or (self.group is not None and group is not self.group):
                return False
            return (
                _same_items(self.undo, self.undo_items)
                and _same_items(self.redo, self.redo_items)
                and len(group.entries) == len(self.group_items) + 1
                and _same_items(group.entries[:-1], self.group_items)
                and getattr(group.entries[-1], "command", None) is command
            )
        current = tuple(self.undo)
        if not current:
            return False
        group = current[-1]
        expected = (*self.undo_items, group)
        limit = getattr(self.undo, "maxlen", None)
        if limit is not None:
            expected = expected[-limit:]
        return (
            _same_items(current, expected)
            and len(group.entries) == 1
            and getattr(group.entries[0], "command", None) is command
            and not self.redo
        )

    def can_rollback(self, command: Any) -> bool:
        if not self.scope.current():
            return False
        if self.book is not None and (
            self.book._undo is not self.book_undo[0]
            or not _same_items(self.book._undo, self.book_undo[1])
            or self.book._redo is not self.book_redo[0]
            or (self.book._redo and not _same_items(self.book._redo, self.book_redo[1]))
        ):
            return False
        if self.pool is not None:
            if any(
                self.bundles.get(key) is not bundle for key, bundle in self.pool._bundles.items()
            ):
                return False
            if any(self.ordinals.get(key) != value for key, value in self.pool._ordinals.items()):
                return False
            for bundle, redo, redo_items, undo, undo_items in self.inactive_redo:
                if (
                    bundle.undo_groups is not undo
                    or not _same_items(undo, undo_items)
                    or bundle.redo_groups is not redo
                    or (redo and not _same_items(redo, redo_items))
                ):
                    return False
        unchanged = (
            _same_items(self.undo, self.undo_items)
            and _same_items(self.redo, self.redo_items)
            and self.canvas._active_history_group is self.group
            and (self.group is None or _same_items(self.group.entries, self.group_items))
        )
        return unchanged or self.recorded(command)

    def restore(self) -> None:
        restore_stack = CanvasCommandCommitGuard._restore_stack
        self.canvas._undo_stack = restore_stack((self.undo, self.undo_items))
        self.canvas._redo_stack = restore_stack((self.redo, self.redo_items))
        self.canvas._active_history_group = self.group
        if self.group is not None:
            self.group.entries[:] = self.group_items
        if self.book is not None:
            self.book._redo = restore_stack(self.book_redo)
        if self.pool is not None:
            self.pool._bundles.clear()
            self.pool._bundles.update(self.bundles)
            self.pool._ordinals.clear()
            self.pool._ordinals.update(self.ordinals)
            for bundle, stack, entries, _undo, _undo_items in self.inactive_redo:
                bundle.redo_groups = restore_stack((stack, entries))
