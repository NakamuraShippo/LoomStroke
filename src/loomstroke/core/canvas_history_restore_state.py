"""Runtime-only failure ownership and payload-free timeline barriers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .canvas_history_entries import _HistoryGroup


@dataclass
class HistoryRestoreOwner:
    """Moves with a page's stacks; only a real clear creates a fresh owner."""

    reason: str | None = None
    barrier: _HistoryGroup | None = None
    barrier_after: bool = False
    _capture_in_progress: bool = field(default=False, init=False, repr=False, compare=False)

    @property
    def blocked(self) -> bool:
        return self.reason is not None

    def block(self, group: Any, *, use_after: bool, reason: str) -> None:
        if self.barrier is None:
            # Import lazily: history entries themselves import Commands.
            from .canvas_history_entries import _HistoryGroup  # noqa: PLC0415

            self.barrier = _HistoryGroup(str(group.label), sequence=int(group.sequence))
            self.barrier_after = use_after
        self.reason = reason

    def peek(self, stack: Any, *, redo: bool) -> _HistoryGroup | None:
        top = stack[-1] if stack else None
        barrier = self.barrier if redo == self.barrier_after else None
        if barrier is None:
            return top
        if top is None or barrier.sequence <= 0:
            return barrier
        if top.sequence <= 0:
            return top
        choose = min if redo else max
        return choose((top, barrier), key=lambda group: group.sequence)
