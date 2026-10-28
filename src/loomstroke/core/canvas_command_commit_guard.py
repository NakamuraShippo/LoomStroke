"""Rollback a generic command when history or canvas post-processing fails."""

from __future__ import annotations

from typing import Any


class CanvasCommandCommitGuard:
    """Keep history references, never duplicate pixel patches or command payloads.

    A successful content-history push can clear inactive-page and book redo
    branches. Their existing bundles and entries therefore stay referenced
    until the entire command commit has succeeded.
    """

    def __init__(self, canvas: Any) -> None:
        self.canvas = canvas
        self.restore_owner = canvas._history()._restore_owner
        self.undo = (canvas._undo_stack, list(canvas._undo_stack))
        self.redo = (canvas._redo_stack, list(canvas._redo_stack))
        self.group = canvas._active_history_group
        self.group_entries = list(self.group.entries) if self.group is not None else []
        self.group_state = (
            (self.group.label, self.group.sequence) if self.group is not None else None
        )
        self.roots = list(canvas._layers)
        self.tree = [
            (layer, list(layer.children), layer.parent_id) for layer in canvas._flatten_layers()
        ]
        self.active_index = canvas._active_index
        self.preview = getattr(canvas, "_vector_preview", None)
        self.preview_layer_id = getattr(canvas, "_vector_preview_layer_id", None)
        self.pages = getattr(
            getattr(canvas, "_history_new_operation_callback", None), "__self__", None
        )
        self.coordinator = getattr(self.pages, "_page_history", None)
        self.book = getattr(self.pages, "_book_history", None)
        self.bundles = dict(self.coordinator._bundles) if self.coordinator is not None else {}
        self.bundle_stacks = [
            (
                bundle,
                bundle.undo_groups,
                list(bundle.undo_groups),
                bundle.redo_groups,
                list(bundle.redo_groups),
            )
            for bundle in self.bundles.values()
        ]
        self.ordinals = dict(self.coordinator._ordinals) if self.coordinator is not None else {}
        self.next_ordinal = self.coordinator._next_ordinal if self.coordinator is not None else 0
        self.book_undo = (
            (self.book._undo, list(self.book._undo)) if self.book is not None else ([], [])
        )
        self.book_redo = (
            (self.book._redo, list(self.book._redo)) if self.book is not None else ([], [])
        )
        self.page_dirty = [(state, state.dirty) for state in getattr(self.pages, "_pages", [])]

    @staticmethod
    def _restore_stack(snapshot: tuple[Any, list[Any]]) -> Any:
        stack, contents = snapshot
        stack.clear()
        stack.extend(contents)
        return stack

    def _restore_history(self) -> None:
        canvas = self.canvas
        canvas._undo_stack = self._restore_stack(self.undo)
        canvas._redo_stack = self._restore_stack(self.redo)
        canvas._history()._restore_owner = self.restore_owner
        canvas._history()._restore_generation += 1
        canvas._active_history_group = self.group
        if self.group is not None:
            self.group.entries[:] = self.group_entries
            if self.group_state is not None:
                self.group.label, self.group.sequence = self.group_state
        if self.book is not None:
            self.book._undo = self._restore_stack(self.book_undo)
            self.book._redo = self._restore_stack(self.book_redo)
        if self.coordinator is not None:
            self.coordinator._bundles.clear()
            self.coordinator._bundles.update(self.bundles)
            self.coordinator._ordinals.clear()
            self.coordinator._ordinals.update(self.ordinals)
            self.coordinator._next_ordinal = self.next_ordinal
            for bundle, undo, undo_entries, redo, redo_entries in self.bundle_stacks:
                bundle.undo_groups = self._restore_stack((undo, undo_entries))
                bundle.redo_groups = self._restore_stack((redo, redo_entries))
        for state, dirty in self.page_dirty:
            state.dirty = dirty

    def rollback(self, command: Any) -> None:
        error = None
        try:
            revert = getattr(command, "revert", None)
            if not callable(revert) or not revert(self.canvas):
                raise RuntimeError("The command rejected rollback.")
        except Exception as exc:
            error = exc
        finally:
            self._restore_history()
            self.canvas._layers[:] = self.roots
            for layer, children, parent_id in self.tree:
                layer.children[:] = children
                layer.parent_id = parent_id
            self.canvas._active_index = self.active_index
            self.canvas._vector_preview = self.preview
            self.canvas._vector_preview_layer_id = self.preview_layer_id
        try:
            self.canvas.bump_layer_texture_versions([layer.id for layer, _, _ in self.tree])
            self.canvas._invalidate_layer_cache()
            self.canvas._recompose()
            self.canvas._emit_history_changed()
        except Exception as exc:
            error = error or exc
        if error is not None:
            raise RuntimeError("Command commit rollback failed.") from error
