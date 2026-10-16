"""Document-local history sequence clock (CT-05 Phase H5a).

One runtime-only monotonic counter shared by the active content history
(``_HistoryGroup.sequence``) and the book history
(``BookHistoryEntry.sequence``) so the ``DocumentHistoryRouter`` can
order mixed-scope Undo/Redo candidates.

Contract:

- values start at 1 and only ever grow (plain Python ``int``; no
  renumbering, no overflow handling);
- only *successful new* history operations claim a value — failed/no-op
  operations, Undo, Redo, and page switches never consume one;
- trims/evictions leave gaps and never rewind the clock;
- Save keeps the clock; Load / New Document / Reset / Recovery reset it;
- never persisted, never wall-clock based, no threads.
"""

from __future__ import annotations


class DocumentHistorySequence:
    """Monotonic document-local sequence source (runtime-only)."""

    def __init__(self) -> None:
        self._next = 1

    def claim(self) -> int:
        """Consume and return the next sequence value."""
        value = self._next
        self._next += 1
        return value

    def peek_next(self) -> int:
        """Return the value the next ``claim()`` would produce (no consume)."""
        return self._next

    def reset(self) -> None:
        """Restart at 1 (document boundary only; Save never calls this)."""
        self._next = 1


__all__ = ["DocumentHistorySequence"]
