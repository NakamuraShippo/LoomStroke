"""One synchronous history attempt, compensating only confirmed successes.

A False/exception never proves that the failing entry left its payload intact.
Compensating the preceding entries therefore cannot authorize a blind retry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from inspect import getattr_static
from typing import Any

from .ai_effect_result_submit import _capture_result_restore_notice
from .canvas_history_entry_outcome import EntryRestoreOutcome, legacy_restore_outcome
from .layer_blend_submit import _capture_blend_restore_notice
from .layer_mask_submit import _capture_mask_restore_notice
from .layer_opacity_submit import _capture_opacity_restore_notice
from .layer_rename_submit import _capture_rename_restore_notice
from .resource_embed_submit import _capture_embed_restore_notice

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroupRestoreOutcome:
    committed: bool
    reason: str | None = None
    prefix_compensated: bool = False


class _GroupRestoreAttempt:
    """All group/entry references here expire when the synchronous call ends."""

    def __init__(self, canvas: Any, history: Any, *, use_after: bool) -> None:
        self.canvas, self.history, self.use_after = canvas, history, use_after
        self.owner, self.generation = history._restore_owner, history._restore_generation
        self.document = getattr(canvas, "_doc", None)
        self.active = getattr(canvas, "_active_layer_id", None), canvas._active_index
        self.undo, self.redo = canvas._undo_stack, canvas._redo_stack
        self.source, self.destination = (
            (self.redo, self.undo) if use_after else (self.undo, self.redo)
        )
        self.group = self.source[-1] if self.source else None
        existing_notice = (
            _capture_rename_restore_notice(canvas, self.group)
            or _capture_embed_restore_notice(canvas, self.group)
            or _capture_blend_restore_notice(canvas, self.group)
            or _capture_opacity_restore_notice(canvas, self.group)
            or _capture_mask_restore_notice(canvas, self.group)
        )
        self.document_notice = _capture_result_restore_notice(canvas, self.group, existing_notice)
        self.completed: list[Any] = []
        self.reason: str | None = None
        self.compensation_attempted = False
        self.prefix_compensated = False

    def _same_owner_context(self) -> bool:
        return (
            self.history._restore_owner is self.owner
            and self.history._restore_generation == self.generation
            and getattr(self.canvas, "_doc", None) is self.document
        )

    def _current(self) -> bool:
        return (
            self._same_owner_context()
            and self.canvas._undo_stack is self.undo
            and self.canvas._redo_stack is self.redo
            and bool(self.source)
            and self.source[-1] is self.group
        )

    def _resume_owned_level(self, baseline: int) -> None:
        depth = getattr(self.canvas, "_defer_recompose_depth", 0)
        try:
            self.canvas.resume_recompose()
        except Exception:
            # Normal resume decrements before rendering. An adapter that
            # raises earlier must not leave our extra level behind. Return
            # exactly one level, never an outer lease or a foreign owner's.
            if (
                self._same_owner_context()
                and depth > baseline
                and getattr(self.canvas, "_defer_recompose_depth", 0) == depth
            ):
                self.canvas._defer_recompose_depth -= 1
            raise

    def _block(self, reason: str) -> None:
        self.reason = reason
        self.owner.block(self.group, use_after=self.use_after, reason=reason)

    def _invoke(self, entry: Any, *, use_after: bool) -> EntryRestoreOutcome:
        try:
            if getattr_static(self.canvas, "_restore_entry_outcome", None) is not None:
                return self.canvas._restore_entry_outcome(entry, use_after=use_after)
            return legacy_restore_outcome(self.canvas._restore_entry(entry, use_after=use_after))
        except Exception as exc:
            logger.warning("History entry boundary failed (%s)", type(exc).__name__)
            return EntryRestoreOutcome(False)

    def _restore_active(self) -> bool:
        if self.active == (
            getattr(self.canvas, "_active_layer_id", None),
            self.canvas._active_index,
        ):
            return True
        from .layer_tree_service import LayerTreeQueryService, sync_active_markers  # noqa: PLC0415

        identifier, index = self.active
        query = LayerTreeQueryService(self.canvas)
        if identifier is None or query.find_layer_by_id_tree(identifier) is None:
            self._block("history_active_unconfirmed")
            return False
        self.canvas._active_layer_id, self.canvas._active_index = identifier, index
        sync_active_markers(self.canvas, query)
        self.canvas._invalidate_layer_cache()
        self.canvas._recompose()
        return self._current()

    def _compensate(self, reason: str) -> None:
        self._block(reason)
        if self.compensation_attempted:
            return
        self.compensation_attempted = True
        for entry in reversed(self.completed):
            if not self._current():
                self._block("history_context_changed")
                return
            if not self._invoke(entry, use_after=not self.use_after).restored:
                self._block("history_inverse_unconfirmed")
                return
            if not self._current():
                self._block("history_context_changed")
                return
        self.prefix_compensated = self._current() and self._restore_active()

    def _entries(self) -> None:
        entries = self.group.entries if self.use_after else reversed(self.group.entries)
        for entry in entries:
            outcome = self._invoke(entry, use_after=self.use_after)
            if not outcome.restored:
                # Only a literal True from the primary proves this entry may
                # join the compensation ledger. Unknown failures remain out.
                if outcome.primary_confirmed and self._current():
                    self.completed.append(entry)
                self._compensate("history_entry_unconfirmed")
                return
            self.completed.append(entry)
            if not self._current():
                self._block("history_context_changed")
                return

    def _recover_after_presentation(self, reason: str) -> None:
        self._block(reason)
        if not self._current() or self.compensation_attempted:
            return
        deferred = False
        baseline = getattr(self.canvas, "_defer_recompose_depth", 0)
        try:
            self.canvas.defer_recompose()
            deferred = True
            self._compensate(reason)
        except Exception as exc:
            self._block("history_recovery_presentation_failed")
            logger.warning("History recovery presentation failed (%s)", type(exc).__name__)
        finally:
            if deferred:
                try:
                    self._resume_owned_level(baseline)
                except Exception as exc:
                    self._block("history_recovery_presentation_failed")
                    logger.warning("History recovery resume failed (%s)", type(exc).__name__)

    def _notify_committed(self) -> None:
        try:
            if self.document_notice is not None:
                self.document_notice.emit_document_change()
        except Exception as exc:
            logger.warning("History restored but its document notification failed: %s", exc)
        try:
            if self.document_notice is None or self.document_notice.current():
                self.canvas._emit_history_changed()
        except Exception as exc:
            # Stack movement committed. Reporting failure could invite replay.
            logger.warning("History restored but its state notification failed: %s", exc)

    def run(self) -> GroupRestoreOutcome:
        if self.group is None:
            return GroupRestoreOutcome(False)
        deferred = False
        baseline = getattr(self.canvas, "_defer_recompose_depth", 0)
        try:
            self.canvas.defer_recompose()
            deferred = True
            self._entries()
        except Exception as exc:
            self._compensate("history_attempt_unconfirmed")
            logger.warning("History attempt failed (%s)", type(exc).__name__)
        finally:
            if deferred:
                try:
                    self._resume_owned_level(baseline)
                except Exception as exc:
                    self._recover_after_presentation("history_resume_failed")
                    logger.warning("History resume failed (%s)", type(exc).__name__)
        if not self._current():
            self._block("history_context_changed")
        if self.reason is not None:
            return GroupRestoreOutcome(False, self.reason, self.prefix_compensated)
        try:
            self.canvas._invalidate_layer_cache()
            self.canvas._recompose()
        except Exception as exc:
            self._recover_after_presentation("history_presentation_failed")
            logger.warning("History final presentation failed (%s)", type(exc).__name__)
            return GroupRestoreOutcome(False, self.reason, self.prefix_compensated)
        if not self._current():
            self._block("history_context_changed")
            return GroupRestoreOutcome(False, self.reason, self.prefix_compensated)
        try:
            # append may need a new deque block. Keep the original group on
            # its source until that allocation has succeeded.
            self.destination.append(self.group)
        except Exception as exc:
            self._recover_after_presentation("history_stack_commit_failed")
            logger.warning("History stack commit failed (%s)", type(exc).__name__)
            return GroupRestoreOutcome(False, self.reason, self.prefix_compensated)
        self.source.pop()
        self._notify_committed()
        return GroupRestoreOutcome(True)


def restore_history_group(canvas: Any, history: Any, *, use_after: bool) -> GroupRestoreOutcome:
    return _GroupRestoreAttempt(canvas, history, use_after=use_after).run()
