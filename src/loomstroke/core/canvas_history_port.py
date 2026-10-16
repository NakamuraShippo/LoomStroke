"""Per-page history bundle detach/attach port for ``SimpleCanvasService``.

CT-05 (page history) Phase H1: the runtime undo/redo stacks can be moved
in and out of the canvas as an owned bundle.  Ownership transfer is an
O(1) move of the deque objects — image patches are never deep-copied.
Bundles are runtime-only and are never persisted to ``.lstroke``.

The page-switch flow does not use this port yet; ``PageServiceImpl``
still clears history on every switch.  Later CT-05 phases wire the
detach/attach pair into page switching to retain per-page history.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

from loomstroke.core.canvas_history_entries import _HistoryGroup

logger = logging.getLogger(__name__)

CLOSE_POLICY_COMMIT = "commit"


@dataclass(slots=True)
class PageHistoryBundle:
    """Owned undo/redo stacks detached from the canvas for one page.

    The deques are moved, not copied; after ``detach_history`` the canvas
    no longer references them until ``attach_history`` hands them back.
    History-group entries keep referencing the layer ids of the page they
    were recorded on, so a bundle must only be re-attached alongside the
    matching page content.
    """

    page_id: str
    undo_groups: deque[_HistoryGroup]
    redo_groups: deque[_HistoryGroup]


class CanvasHistoryPortService:
    """Detach/attach boundary around the canvas history stacks (CT-05 H1)."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def close_pending_group(self, policy: str = CLOSE_POLICY_COMMIT) -> bool:
        """Close any in-flight transaction so the stacks are safe to move.

        ``commit`` flushes pending deferred paint and pushes the active
        group through the existing ``end_transaction()`` path.  Returns
        True when no pending transaction/group remains afterwards.
        """
        if policy != CLOSE_POLICY_COMMIT:
            raise ValueError(f"unsupported close policy: {policy!r}")
        canvas = self._canvas
        filter_pen = getattr(
            canvas, "_filter_pen_service", getattr(canvas, "_effect_pen_service", None)
        )
        if filter_pen is not None:
            filter_pen.finalize()
        if canvas._in_transaction:
            canvas.end_transaction()
        elif canvas._active_history_group is not None:
            group = canvas._active_history_group
            canvas._active_history_group = None
            if group.entries:
                canvas._push_history_group(group)
        return not canvas._in_transaction and canvas._active_history_group is None

    def detach_history(self, page_id: str) -> PageHistoryBundle:
        """Move ownership of the undo/redo stacks into a per-page bundle.

        A pending transaction is committed into the outgoing stacks first
        so no recorded edit is lost.  The canvas is left with fresh empty
        stacks (undo keeps the configured ``history_limit`` bound).
        """
        canvas = self._canvas
        self.close_pending_group(CLOSE_POLICY_COMMIT)
        bundle = PageHistoryBundle(
            page_id=str(page_id),
            undo_groups=canvas._undo_stack,
            redo_groups=canvas._redo_stack,
        )
        canvas._undo_stack = deque(maxlen=canvas._history_limit)
        canvas._redo_stack = deque()
        canvas._active_history_group = None
        canvas._emit_history_changed()
        return bundle

    def attach_history(self, bundle: PageHistoryBundle) -> None:
        """Hand a previously detached bundle back to the canvas.

        The caller must have closed the outgoing page's pending group
        (``detach_history`` does this); attaching over an in-flight
        transaction would interleave two pages' edits, so it is refused
        loudly instead of failing silently.
        """
        canvas = self._canvas
        if canvas._in_transaction or canvas._active_history_group is not None:
            raise RuntimeError(
                "attach_history called while a history transaction is pending; "
                "close the pending group on the outgoing page first"
            )
        canvas._undo_stack = bundle.undo_groups
        canvas._redo_stack = bundle.redo_groups
        canvas._emit_history_changed()


__all__ = ["CLOSE_POLICY_COMMIT", "CanvasHistoryPortService", "PageHistoryBundle"]
