"""Call-local state for restoring one standard page switch without an installer."""

from __future__ import annotations

from typing import Any

from .canvas_history_port import PageHistoryBundle
from .canvas_model_recovery import _CanvasModelRecovery

_HISTORY_FIELDS = (
    "_undo_stack",
    "_redo_stack",
    "_active_history_group",
    "_in_transaction",
    "_transaction_label",
)


class _PageSwitchRecovery:
    """Retain references after source capture, before history custody moves.

    Standard page installation replaces the tree and geometry without mutating
    these source layers. Cached paths/images may be cleared in place by the
    installer, so rollback starts with fresh caches and a full resource refresh.
    No encoder, page installer, or history attach is called by ``restore``.
    """

    def __init__(self, service: Any, source_id: str, target_id: str) -> None:
        # The existing bundle module imports PageService for document opening;
        # importing the reusable plain-state class here avoids an eager cycle.
        from .image_document_state import ImageDocumentStateBundle  # noqa: PLC0415

        self._service = service
        self._canvas = service._canvas
        self._history = self._canvas._history()
        self._pool = service._page_history
        self._source_id, self._target_id = source_id, target_id
        self._source_bundle: PageHistoryBundle | None = None
        self._target_bundle = self._pool._bundles.get(target_id)
        self._target_ordinal = self._pool._ordinals.get(target_id)
        self._model = _CanvasModelRecovery(self._canvas, reason="page_switch_recovery")
        self._bundles = [
            ImageDocumentStateBundle.prepare(self._canvas, dict.fromkeys(_HISTORY_FIELDS)),
            ImageDocumentStateBundle.prepare(self._history, {"_restore_owner": None}),
        ]

    def take_source(self, bundle: PageHistoryBundle) -> None:
        """Keep even an empty bundle, whose owner is not retained by the pool."""
        self._source_bundle = bundle

    def restore(self) -> None:
        """Restore the model first; callers send notifications separately."""
        generation = self._history._restore_generation + 1
        self._model.restore()
        for bundle in self._bundles:
            bundle.restore()
        self._history.__dict__["_restore_generation"] = generation
        pool = self._pool
        if (
            self._source_bundle is not None
            and pool._bundles.get(self._source_id) is self._source_bundle
        ):
            pool._bundles.pop(self._source_id)
            pool._ordinals.pop(self._source_id, None)
        current = pool._bundles.get(self._target_id)
        if current is None or current is self._target_bundle:
            if self._target_bundle is not None:
                pool._bundles[self._target_id] = self._target_bundle
            if self._target_ordinal is None:
                pool._ordinals.pop(self._target_id, None)
            else:
                pool._ordinals[self._target_id] = self._target_ordinal
        # Keep the monotonic ordinal clock and unrelated pool entries intact.
        self._service.__dict__["_active_page_id"] = self._source_id
