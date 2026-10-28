"""Canvas service HistoryApi mixin."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import (
    QImage,
)

from loomstroke.core.canvas_history_entries import (
    HistoryEntry,
    _HistoryGroup,
    _HistoryPatch,
)
from loomstroke.core.canvas_history_entry_outcome import (
    EntryRestoreOutcome,
    legacy_restore_outcome,
    uses_default_restore_method,
)
from loomstroke.core.canvas_history_port import CLOSE_POLICY_COMMIT, PageHistoryBundle
from loomstroke.core.commands import (
    Command,
    UpdateSelectionMaskCommand,
)
from loomstroke.host_api import LayerRef

logger = logging.getLogger(__name__)
_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LayerValue: TypeAlias = LayerRef | Mapping[str, int] | bool | str | int | None


if TYPE_CHECKING:
    from loomstroke.core.canvas_service_simple_accessors import CanvasServiceAccessorsMixin

    class _CanvasServiceHistoryApiBase(CanvasServiceAccessorsMixin):
        _history_adapter: Any | None
        _undo_stack: Any
        _redo_stack: Any

else:

    class _CanvasServiceHistoryApiBase:
        pass


class CanvasServiceHistoryApiMixin(_CanvasServiceHistoryApiBase):
    def _record_selection_history(self, cmd: UpdateSelectionMaskCommand) -> None:
        self._selection_history().record_selection_history(cmd)

    def _record_selection_snapshot_history(self, before: Any | None, after: Any | None) -> None:
        self._selection_history().record_snapshot_history(before, after)

    def _record_selection_command_history(self, cmd: Any, *, label: str) -> None:
        self._selection_history().record_content_command_history(cmd, label=label)

    def _fit_selection_mask(self, mask: QImage) -> QImage:
        return self._selection_history().fit_selection_mask(mask)

    def _selection_snapshot_image(self, mask: Any | None) -> QImage:
        return self._selection_history().selection_snapshot_image(mask)

    def _empty_selection_mask(self) -> QImage:
        return self._selection_history().empty_selection_mask()

    def _numpy_to_selection_mask(self, mask: NDArray[Any]) -> QImage:
        return self._selection_history().numpy_to_selection_mask(mask)

    def _history_snapshot(self) -> tuple[bool, bool, int, int]:
        return self._history().snapshot()

    def _emit_history_changed(self) -> None:
        self._history().emit_changed()

    def history_can_undo(self) -> bool:
        """Return True when at least one undo group is available."""

        adapter = getattr(self, "_history_adapter", None)
        if adapter is not None:
            return cast("bool", adapter.can_undo())
        return bool(self._undo_stack) and not self._history_restore_blocked()

    def history_can_redo(self) -> bool:
        """Return True when redo stack has entries."""

        adapter = getattr(self, "_history_adapter", None)
        if adapter is not None:
            return cast("bool", adapter.can_redo())
        return bool(self._redo_stack) and not self._history_restore_blocked()

    def history_undo(self) -> bool:
        """Undo the most recent history group."""

        adapter = getattr(self, "_history_adapter", None)
        if adapter is not None:
            return cast("bool", adapter.undo())
        return self._history_undo_direct()

    def _history_undo_direct(self) -> bool:
        """Legacy undo implementation retained behind ``HistoryAdapter``."""
        return self._history()._undo_direct()

    def history_redo(self) -> bool:
        """Redo the most recently undone group."""

        adapter = getattr(self, "_history_adapter", None)
        if adapter is not None:
            return cast("bool", adapter.redo())
        return self._history_redo_direct()

    def _history_redo_direct(self) -> bool:
        """Legacy redo implementation retained behind ``HistoryAdapter``."""
        return self._history()._redo_direct()

    def history_undo_count(self) -> int:
        """Return the number of undoable operations."""

        adapter = getattr(self, "_history_adapter", None)
        if adapter is not None:
            return cast("int", adapter.undo_count())
        return len(self._undo_stack)

    def history_redo_count(self) -> int:
        """Return the number of redoable operations."""

        adapter = getattr(self, "_history_adapter", None)
        if adapter is not None:
            return cast("int", adapter.redo_count())
        return len(self._redo_stack)

    def history_clear(self) -> None:
        """Clear undo/redo history and pending transactions."""

        adapter = getattr(self, "_history_adapter", None)
        if adapter is not None:
            adapter.clear()
            return
        self._history_clear_direct()

    def _history_clear_direct(self) -> None:
        """Legacy history clear retained behind ``HistoryAdapter``."""
        self._history().clear_direct()

    def _history_close_pending_group(self, policy: str = CLOSE_POLICY_COMMIT) -> bool:
        """Close any pending history transaction/group (CT-05 page-history port)."""
        return self._history_port().close_pending_group(policy)

    def _history_drop_redo_branch(self) -> None:
        """Discard the canvas redo branch (H6 global new-operation rule).

        Used when a *book* operation invalidates the document-wide redo
        future.  Emits history-changed only when something was dropped.
        """
        if self._redo_stack:
            self._redo_stack.clear()
            self._emit_history_changed()

    def _history_set_new_operation_callback(self, callback: Callable[[], None] | None) -> None:
        """Notify page-history ownership of brand-new content operations.

        Core-internal (H6): the bookkeeping service invokes this after a
        new group enters the undo stack so the document-wide redo
        branches (book + inactive pages) can be invalidated.  Without a
        callback (standalone canvas) behaviour is unchanged.
        """
        self._history_new_operation_callback = callback

    def _history_set_changed_callback(self, callback: Callable[[], None] | None) -> None:
        """Route history-changed notifications to a unified listener (H5b).

        Core-internal: with a callback set, ``emit_changed`` forwards to
        it (the unified history service publishes the public events);
        with ``None`` the canvas publishes HISTORY_CHANGED directly as
        before (standalone/legacy behaviour).
        """
        self._history_changed_callback = callback

    def _history_set_sequence_source(self, source: Callable[[], int] | None) -> None:
        """Inject the document-local sequence source (CT-05 H5a).

        Core-internal: ``PageServiceImpl`` shares one
        ``DocumentHistorySequence.claim`` across content and book
        history.  Without an injected source (standalone canvas, legacy
        use) groups keep the unsequenced default and behave as before.
        """
        self._history_sequence_source = source

    def _history_peek_undo_group(self) -> _HistoryGroup | None:
        """Return the next-undo group without mutating any state.

        Never commits a pending transaction, never emits events, never
        claims a sequence (CT-05 H5a router surface).
        """
        return self._history()._peek_group(redo=False)

    def _history_peek_redo_group(self) -> _HistoryGroup | None:
        """Return the next-redo group (stack top) without mutating state."""
        return self._history()._peek_group(redo=True)

    def _history_restore_blocked(self) -> bool:
        return self._history()._restore_blocked()

    def _history_set_failure_scope_factory(self, factory: Callable[[Any], Any] | None) -> None:
        self._history()._failure_scope_factory = factory

    def _history_detach_bundle(self, page_id: str) -> PageHistoryBundle:
        """Move ownership of the undo/redo stacks into a per-page bundle."""
        return self._history_port().detach_history(page_id)

    def _history_detach_prepared_bundle(self, page_id: str) -> PageHistoryBundle:
        """Move stacks whose pending work and source snapshot were prepared."""
        return self._history_port()._detach_prepared_history(page_id)

    def _history_attach_bundle(self, bundle: PageHistoryBundle) -> None:
        """Hand a previously detached per-page bundle back to the canvas."""
        self._history_port().attach_history(bundle)

    def _record_history_patch(self, label: str, patch: _HistoryPatch) -> None:
        self._history().record_patch(label, patch)

    @staticmethod
    def _history_patch_from_images(
        layer_id: str,
        bounds: QRect,
        before: QImage,
        after: QImage,
        metadata: dict[str, Any] | None = None,
    ) -> _HistoryPatch:
        return _HistoryPatch.from_images(layer_id, bounds, before, after, metadata)

    def _record_history_command(self, label: str, command: Command) -> None:
        self._history().record_command(label, command)

    def _push_history_group(self, group: _HistoryGroup) -> None:
        self._history().push_group(group)

    def _restore_entry(self, entry: HistoryEntry, *, use_after: bool) -> bool:
        return self._history().restore_entry(entry, use_after=use_after)

    def _restore_entry_outcome(
        self, entry: HistoryEntry, *, use_after: bool
    ) -> EntryRestoreOutcome:
        if not uses_default_restore_method(self._restore_entry, _DEFAULT_CANVAS_RESTORE_ENTRY):
            return legacy_restore_outcome(self._restore_entry(entry, use_after=use_after))
        return self._history()._restore_entry_outcome(entry, use_after=use_after)

    def _restore_command(self, command: Command, *, use_after: bool) -> bool:
        return self._history().restore_command(command, use_after=use_after)

    def _save_flood_fill_alpha_for_history_redo(
        self,
        command: Command,
    ) -> tuple[NDArray[Any] | None, Any | None]:
        return self._history().save_flood_fill_alpha_for_history_redo(command)

    def _refresh_flood_fill_history_restore(self, command: Command) -> None:
        self._history().refresh_flood_fill_history_restore(command)

    def _restore_patch(self, patch: _HistoryPatch, *, use_after: bool) -> None:
        self._history().restore_patch(patch, use_after=use_after)


_DEFAULT_CANVAS_RESTORE_ENTRY = CanvasServiceHistoryApiMixin._restore_entry
_DEFAULT_CANVAS_DETACH_BUNDLE = CanvasServiceHistoryApiMixin._history_detach_bundle
