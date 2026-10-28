"""Close or commit only the Gradient owner at normal edit boundaries."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .canvas_history_entries import _HistoryCommand, _HistoryPatch
from .gradient_preview_owner import GradientPreviewOwner
from .layer_command_commit_context import (
    _CommandHistoryReceipt,
    _LayerCommandContext,
    _same_items,
)
from .native_cmyk.patch_command import NativeCmykPatchCommand

logger = logging.getLogger(__name__)


class _GradientHistoryReceipt(_CommandHistoryReceipt):
    """Accept one standard Gradient result, including its component/native patch."""

    def __init__(self, canvas: Any, owner: GradientPreviewOwner) -> None:
        super().__init__(canvas, owner.scope)
        self.owner = owner
        self.page_dirty = [
            (state, state.dirty) for state in getattr(self.scope.pages, "_pages", [])
        ]

    def _owned_entry(self, entry: Any, command: Any) -> bool:
        recorded = getattr(entry, "command", None)
        if isinstance(entry, _HistoryCommand) and recorded is command:
            return True
        if self.owner.native is not None:
            return (
                isinstance(entry, _HistoryCommand)
                and isinstance(recorded, NativeCmykPatchCommand)
                and recorded.layer_id == self.owner.layer_id
                and recorded.rect == (0, 0, self.owner.target.shape[1], self.owner.target.shape[0])
            )
        return (
            isinstance(entry, _HistoryPatch)
            and entry.layer_id == self.owner.layer_id
            and getattr(entry, "metadata", {}).get("command") == "channel_edit"
        )

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
                and self._owned_entry(group.entries[-1], command)
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
            and self._owned_entry(group.entries[0], command)
            and not self.redo
        )

    def restore(self) -> None:
        super().restore()
        self.canvas._history()._restore_generation += 1
        for state, dirty in self.page_dirty:
            state.dirty = dirty


def close_gradient_preview_before_edit(canvas: Any) -> bool:
    owner = getattr(canvas, "_gradient_preview_owner", None)
    if not isinstance(owner, GradientPreviewOwner):
        return True
    if owner.phase == "committing" and not owner.foreign_document():
        return False
    context = _LayerCommandContext(canvas)
    getter = getattr(canvas, "_get_active_layer", None)
    active = getter() if callable(getter) else None
    if not owner.cancel():
        return False
    return (
        context.current()
        and getattr(canvas, "_gradient_preview_owner", None) is None
        and (not callable(getter) or getter() is active)
    )


def dispatch_with_gradient_preview(canvas: Any, command: Any, dispatch: Callable[[], bool]) -> bool:
    if getattr(command, "_gradient_preview_aborted", False) is True:
        return False
    owner = getattr(canvas, "_gradient_preview_owner", None)
    command_owner = getattr(command, "_gradient_preview_owner", None)
    if not isinstance(command_owner, GradientPreviewOwner):
        return bool(dispatch()) if close_gradient_preview_before_edit(canvas) else False
    if owner is not command_owner or owner.command is not command:
        return False
    if not owner.can_edit(canvas):
        owner.cancel()
        return False
    return _dispatch_owned_gradient(canvas, command, dispatch, owner)


def _dispatch_owned_gradient(
    canvas: Any, command: Any, dispatch: Callable[[], bool], owner: GradientPreviewOwner
) -> bool:
    receipt = _GradientHistoryReceipt(canvas, owner)
    try:
        prepared = owner.prepare(canvas, command)
    except Exception:
        logger.exception("Gradient preview command preparation failed")
        return False
    if not prepared:
        return False
    try:
        success = bool(dispatch())
    except Exception:
        logger.exception("Gradient preview command submission failed")
        success = False
    if success and owner.source_matches(attempt=True) and receipt.recorded(command):
        owner.phase = "submitted"
        return True
    try:
        if receipt.can_rollback(command) and owner.restore(attempt=True):
            receipt.restore()
            owner.phase = "recovered"
            owner.close()
            canvas._recompose()
    except Exception:
        # An uncertain restore keeps its owner and original; a display-only
        # failure after confirmed recovery must not apply the inverse again.
        logger.exception("Gradient original recovery or display refresh failed")
    return False
