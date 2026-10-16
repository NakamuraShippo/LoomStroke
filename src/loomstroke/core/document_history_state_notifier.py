"""Unified document-history state change notifier (CT-05 Phase H5b).

``DocumentHistoryStateNotifier`` is the internal glue between the
history *sources* (canvas content history, ``PageCommandService`` book
history, page switches) and the single authoritative history service
(``MinimalHistoryService`` in production).  Sources only ``mark_changed()``;
the listener — when one is bound — re-evaluates the unified router state
and publishes the public HISTORY_STATE / HISTORY_CHANGED pair.

This is deliberately NOT an EventBus reflection: subscribing to
``Events.HISTORY_CHANGED`` and re-publishing the same event would recurse
and double-notify.  Sources call this object directly.

Logical operations (page command execute/undo/redo, page switches,
document loads) wrap their internal mutations in ``batch()``: nested
batches are supported, dirty marks accumulate, and the listener runs
exactly once when the outermost batch exits — so the UI never observes
detach/attach or mid-command intermediate states.  Without a bound
listener every call is a cheap no-op (standalone canvas / unit tests).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class DocumentHistoryStateNotifier:
    """Collect history-source change marks and notify one listener."""

    def __init__(self) -> None:
        self._listener: Callable[[], None] | None = None
        self._depth = 0
        self._dirty = False

    def set_listener(self, listener: Callable[[], None] | None) -> None:
        """Bind (or unbind with ``None``) the unified-state listener."""
        self._listener = listener

    def mark_changed(self) -> None:
        """Record that a history source changed.

        Inside a batch the mark is deferred; outside one the listener is
        notified immediately.  Safe (and free) without a listener.
        """
        if self._depth > 0:
            self._dirty = True
            return
        self._notify()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Coalesce marks of one logical operation into a single notify.

        Nested batches defer to the outermost one; the exit path runs in
        a ``finally`` so an exception mid-operation can never leave the
        depth counter broken (the listener still runs, reporting the
        *actual* current state).
        """
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0 and self._dirty:
                self._dirty = False
                self._notify()

    def _notify(self) -> None:
        listener = self._listener
        if listener is None:
            return
        try:
            listener()
        except Exception:
            # The listener is UI-facing; a failure there must not break
            # the core history operation that triggered it — but it is
            # logged, never silently dropped.
            logger.exception("Unified document-history state listener failed")


__all__ = ["DocumentHistoryStateNotifier"]
