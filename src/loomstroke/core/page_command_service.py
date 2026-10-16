"""Runtime-only book history stack for page structure commands (CT-05 H3).

``PageCommandService`` owns the undo/redo stacks for page structure
operations (add / rename / move as of Phase H3).  The stacks are fully
independent from the canvas per-page content history (H1/H2): book
commands never enter a ``PageHistoryBundle`` and never touch content
redo branches.

The book history is never persisted (``.lstroke``, autosave, page
metadata) and is cleared at every document boundary via ``clear()``.
It is intentionally NOT connected to the UI Undo/Redo actions until the
Phase H5 document history router exists; callers interact with it
through core-internal surfaces only.

CT-05 H4d2: this service is also the single authority for the
document-wide retained-memory budget.  One soft budget covers both the
payloads book entries keep alive outside the document AND the inactive
content bundles owned by the ``PageHistoryCoordinator``.  Trims run
through an injectable scheduler (production wires a Qt idle callback;
without a scheduler the trim is synchronous) with bounded work per
callback and generation-based invalidation across document boundaries.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loomstroke.core.document_history_sequence import DocumentHistorySequence
from loomstroke.core.document_history_state_notifier import DocumentHistoryStateNotifier
from loomstroke.core.page_commands import PageCommand
from loomstroke.core.page_history_memory import estimate_history_group_bytes

if TYPE_CHECKING:
    from loomstroke.core.page_history_coordinator import PageHistoryCoordinator

logger = logging.getLogger(__name__)

# Soft ceiling for the extra memory the whole document history keeps
# alive outside the document — CT-05 H4d1/H4d2.  One single budget covers
# both the book history payloads (off-document PageSnapshots, thumbnail
# bytes, delete-command bundles) and the inactive content history bundles
# owned by the PageHistoryCoordinator; the two are never budgeted
# separately.  256 MiB holds roughly two uncompressed 4K page snapshots
# (~128 MiB each per the AUTOSAVE-PERF-02 measurements), which keeps
# recent structure operations undoable without letting a long session
# grow unbounded.  Internal constant by design: no settings key, never
# persisted, overridable per-instance for tests.
_DEFAULT_MEMORY_BUDGET_BYTES = 256 * 1024 * 1024

# Target work per scheduled trim callback (CT-05 H4d2).  Well below the
# 100 ms main-thread blocking limit; unfinished work re-schedules itself.
_TRIM_WORK_BUDGET_SECONDS = 0.008


@dataclass(slots=True)
class BookHistoryEntry:
    """One executed page structure command with router-ready metadata."""

    sequence: int
    label_key: str
    command: PageCommand
    affected_page_ids: tuple[str, ...]


@dataclass(slots=True)
class BookHistoryTrimResult:
    """Outcome of one memory-budget trim pass (internal API).

    ``evicted_entries`` keeps its H4d1 meaning (book entries evicted);
    the H4d2 fields break the same pass down further.
    """

    evicted_entries: int
    retained_bytes: int
    over_budget: bool
    evicted_book_entries: int = 0
    evicted_content_groups: int = 0
    work_remaining: bool = False


class PageCommandService:
    """Execute page commands and own the book undo/redo stacks."""

    def __init__(
        self,
        service: Any,
        *,
        memory_budget_bytes: int = _DEFAULT_MEMORY_BUDGET_BYTES,
        coordinator: PageHistoryCoordinator | None = None,
        trim_scheduler: Callable[[Callable[[], None]], None] | None = None,
        clock: Callable[[], float] | None = None,
        sequence_clock: DocumentHistorySequence | None = None,
        state_notifier: DocumentHistoryStateNotifier | None = None,
        content_redo_invalidator: Callable[[], None] | None = None,
    ) -> None:
        self._service = service
        self._undo: list[BookHistoryEntry] = []
        self._redo: list[BookHistoryEntry] = []
        # CT-05 H5a: entry sequences come from the document-local clock
        # shared with the content history.  Standalone use (unit tests,
        # no PageService) falls back to a private clock and keeps the
        # H3 behaviour (1-based, monotonic, gap-tolerant).
        self._sequence_clock = (
            sequence_clock if sequence_clock is not None else DocumentHistorySequence()
        )
        # CT-05 H5b: unified-state change marks.  Standalone use gets a
        # listenerless notifier, so every notification is a no-op and the
        # pre-H5b behaviour (no direct HISTORY_CHANGED from book ops)
        # is preserved.
        self._state_notifier = (
            state_notifier if state_notifier is not None else DocumentHistoryStateNotifier()
        )
        # CT-05 H6: a successful *new* book command invalidates the
        # document-wide content redo branches (active canvas + inactive
        # bundles) — one single redoable future.  Undo/redo of existing
        # entries never invalidates.  None keeps the pre-H6 behaviour.
        self._content_redo_invalidator = content_redo_invalidator
        self._memory_budget_bytes = int(memory_budget_bytes)
        self._coordinator = coordinator
        self._trim_scheduler = trim_scheduler
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        # Document-boundary generation: a callback scheduled before a
        # clear() must never trim the next document's history (H4d2).
        self._trim_generation = 0
        self._pending_trim_generation: int | None = None

    # ------------------------------------------------------------------
    # Introspection
    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_count(self) -> int:
        return len(self._undo)

    def redo_count(self) -> int:
        return len(self._redo)

    def peek_undo_entry(self) -> BookHistoryEntry | None:
        """Return the next-undo entry without mutating any state.

        Non-mutating router surface (CT-05 H5a): no pop, no events, no
        sequence consumption.
        """
        return self._undo[-1] if self._undo else None

    def peek_redo_entry(self) -> BookHistoryEntry | None:
        """Return the next-redo entry (``_redo[-1]``) without mutating state."""
        return self._redo[-1] if self._redo else None

    def _off_document_page_context(self, page_id: str) -> tuple[str | None, int | None] | None:
        """Read-only off-document page hint for history UI labels (H5c).

        A page that no longer lives in the document (applied delete,
        undone add/duplicate) still has its snapshot in custody of a book
        command; this returns its custom name and captured one-based
        index so the UI never has to show a raw page id.  Strictly
        read-only: no ownership flags change, nothing is copied, and the
        newest custody wins.  Returns ``None`` when no command retains
        the page.
        """
        for entry in (*reversed(self._undo), *reversed(self._redo)):
            command = entry.command
            if page_id not in entry.affected_page_ids:
                continue
            if not getattr(command, "retains_off_document_payload", False):
                continue
            page = getattr(command, "page", None)
            if page is None or getattr(page, "id", None) != page_id:
                continue
            name = getattr(page, "name", None)
            custom_name = name.strip() if isinstance(name, str) and name.strip() else None
            index = getattr(command, "index", None)
            if not isinstance(index, int):
                index = getattr(command, "fallback_index", None)
            one_based = index + 1 if isinstance(index, int) and index >= 0 else None
            return (custom_name, one_based)
        return None

    # ------------------------------------------------------------------
    # Mutation
    def execute(self, command: PageCommand) -> bool:
        """Apply ``command``; record it only on success.

        Failed or no-op commands never enter the stack, and a successful
        command discards only the *book* redo branch — content history
        redo branches (active or stored) are untouched.
        """
        # The whole logical operation — apply (which may switch pages and
        # move history bundles), stack append, redo clear, and the
        # synchronous trim — is one notifier batch: the unified listener
        # runs once afterwards and never observes an intermediate state
        # (H5b).  A failed apply marks nothing new itself, but any real
        # mutations its inner steps made still surface as actual state.
        with self._state_notifier.batch():
            try:
                succeeded = bool(command.apply(self._service))
            except Exception:
                logger.exception("Page command %s failed to apply", command.label_key)
                return False
            if not succeeded:
                return False
            # The sequence is claimed only after a successful apply, right
            # before the entry enters the stack — failed/no-op commands never
            # leave a gap, and undo/redo reuse the entry without re-claiming.
            entry = BookHistoryEntry(
                sequence=self._sequence_clock.claim(),
                label_key=command.label_key,
                command=command,
                affected_page_ids=tuple(command.affected_page_ids),
            )
            self._undo.append(entry)
            self._redo.clear()
            if self._content_redo_invalidator is not None:
                self._content_redo_invalidator()
            self._state_notifier.mark_changed()
            self.request_memory_trim()
        return True

    def undo(self) -> bool:
        """Revert the newest entry; on failure the entry stays undoable."""
        if not self._undo:
            return False
        with self._state_notifier.batch():
            entry = self._undo[-1]
            try:
                succeeded = bool(entry.command.revert(self._service))
            except Exception:
                logger.exception("Page command %s failed to revert", entry.label_key)
                return False
            if not succeeded:
                return False
            self._undo.pop()
            self._redo.append(entry)
            self._state_notifier.mark_changed()
            self.request_memory_trim()
        return True

    def redo(self) -> bool:
        """Re-apply the newest undone entry; on failure it stays redoable."""
        if not self._redo:
            return False
        with self._state_notifier.batch():
            entry = self._redo[-1]
            try:
                succeeded = bool(entry.command.apply(self._service))
            except Exception:
                logger.exception("Page command %s failed to redo", entry.label_key)
                return False
            if not succeeded:
                return False
            self._redo.pop()
            self._undo.append(entry)
            self._state_notifier.mark_changed()
            self.request_memory_trim()
        return True

    # ------------------------------------------------------------------
    # Retained-memory budget (CT-05 H4d1/H4d2).  One document-wide soft
    # budget covers the book payloads and the coordinator-owned inactive
    # content bundles; active canvas history and pending groups are never
    # counted or trimmed.
    def _estimate_book_entry_bytes(self, entry: BookHistoryEntry, seen: set[int]) -> int:
        estimate = getattr(entry.command, "estimated_retained_bytes", None)
        if not callable(estimate):
            return 0
        try:
            return int(estimate(seen))
        except Exception:
            logger.exception(
                "Failed to estimate retained bytes for book entry seq=%d (%s)",
                entry.sequence,
                entry.label_key,
            )
            return 0

    def _estimated_book_retained_bytes(self, seen: set[int]) -> int:
        """Book-only estimate (internal diagnostics/tests): payloads the
        book entries themselves keep alive outside the document."""
        total = 0
        for entry in (*self._undo, *self._redo):
            total += self._estimate_book_entry_bytes(entry, seen)
        return total

    def estimated_retained_bytes(self) -> int:
        """Document-wide estimate: book payloads + inactive content bundles.

        Both sides share one ``seen`` set so an object in ownership
        transfer (e.g. a delete-command bundle) is counted exactly once.
        """
        seen: set[int] = set()
        total = self._estimated_book_retained_bytes(seen)
        if self._coordinator is not None:
            total += self._coordinator.estimated_retained_bytes(seen)
        return total

    def memory_budget_bytes(self) -> int:
        return self._memory_budget_bytes

    def is_over_memory_budget(self) -> bool:
        return self.estimated_retained_bytes() > self._memory_budget_bytes

    def request_memory_trim(self) -> None:
        """Request a document-wide trim at the next safe point.

        With an injected scheduler (production: a Qt zero-delay timer)
        consecutive requests coalesce into one pending callback whose
        work is bounded; unfinished work re-schedules itself.  Without a
        scheduler (unit tests, non-Qt use) the trim runs synchronously to
        completion for determinism.
        """
        if self._trim_scheduler is None:
            self.trim_to_memory_budget()
            return
        if self._pending_trim_generation == self._trim_generation:
            return  # already scheduled for this document generation
        generation = self._trim_generation
        self._pending_trim_generation = generation
        self._trim_scheduler(lambda: self._run_scheduled_trim(generation))

    def _run_scheduled_trim(self, generation: int) -> None:
        """One bounded trim callback; stale generations are no-ops.

        Exceptions propagate to the scheduler's caller — a failing trim
        must be visible, never silently swallowed.
        """
        if self._pending_trim_generation == generation:
            self._pending_trim_generation = None
        if generation != self._trim_generation:
            # Scheduled before a document boundary: the history it was
            # meant to trim no longer exists.
            return
        deadline = self._clock() + _TRIM_WORK_BUDGET_SECONDS
        result = self._trim_pass(deadline=deadline)
        if result.evicted_book_entries > 0:
            # Book evictions change the unified undo/redo counts; pure
            # inactive-content evictions never touch router candidates,
            # so they stay silent (H5b).
            self._state_notifier.mark_changed()
        if result.work_remaining:
            self.request_memory_trim()

    def trim_to_memory_budget(self) -> BookHistoryTrimResult:
        """Synchronously trim to the budget (explicit/schedulerless path).

        Reuses the same batch algorithm as the scheduled callback but
        loops passes until the authoritative re-estimate honors the
        budget or only protected entries remain.
        """
        evicted_book = 0
        evicted_content = 0
        with self._state_notifier.batch():
            while True:
                result = self._trim_pass(deadline=None)
                evicted_book += result.evicted_book_entries
                evicted_content += result.evicted_content_groups
                if not result.over_budget:
                    break
                if result.evicted_book_entries == 0 and result.evicted_content_groups == 0:
                    break  # only protected entries remain (soft over-budget)
            if evicted_book > 0:
                # Same rule as the scheduled path: only book evictions
                # change the unified state (H5b).
                self._state_notifier.mark_changed()
        return BookHistoryTrimResult(
            evicted_entries=evicted_book,
            retained_bytes=result.retained_bytes,
            over_budget=result.over_budget,
            evicted_book_entries=evicted_book,
            evicted_content_groups=evicted_content,
            work_remaining=False,
        )

    def _estimate_document_wide(self, seen: set[int]) -> int:
        total = self._estimated_book_retained_bytes(seen)
        if self._coordinator is not None:
            total += self._coordinator.estimated_retained_bytes(seen)
        return total

    def _has_evictable_candidates(self) -> bool:
        # CT-05 H6: redo entries are never evicted (self-limiting under
        # the single-timeline rule) — only undo-side candidates count.
        if self._coordinator is not None and self._coordinator.has_evictable_groups():
            return True
        return len(self._undo) > 1

    def _trim_pass(self, *, deadline: float | None) -> BookHistoryTrimResult:
        """One batch eviction pass over the document-wide budget.

        The full retained estimate is computed exactly twice per pass —
        once up front, once (authoritative) at the end — while each
        eviction only subtracts its own victim's estimate, so a pass over
        N candidates is O(N), not O(N^2) (H4d2; H4d1 re-estimated every
        stack per eviction).

        Eviction order: undo-side content history groups of the least-
        recently-active inactive page first (oldest first); book UNDO
        entries (``_undo[0]``) only after no inactive undo group remains.
        CT-05 H6: redo entries/groups are NEVER evicted — the single
        timeline drops every redo branch on the next new operation, so
        redo custody is transient and keeping it is a soft over-budget
        state at worst.  Sequences are never renumbered and the newest
        undo recovery point stays protected.  The trim never touches the
        document, active canvas history, pending groups, or events.
        """
        budget = self._memory_budget_bytes
        retained = self._estimate_document_wide(set())
        evicted_book = 0
        evicted_content = 0
        work_remaining = False
        protected_only = False
        while retained > budget:
            if deadline is not None and self._clock() >= deadline:
                work_remaining = True
                break
            group = (
                self._coordinator.evict_oldest_inactive_group()
                if self._coordinator is not None
                else None
            )
            if group is not None:
                evicted_content += 1
                retained -= estimate_history_group_bytes(group, seen=set())
                continue
            if len(self._undo) > 1:
                victim = self._undo.pop(0)
            else:
                # CT-05 H6: the newest undo recovery point is protected as
                # before, and redo entries are NEVER evicted — under the
                # single timeline any new operation drops the whole redo
                # future, so redo custody frees itself; evicting it here
                # would silently destroy the only redoable future
                # (H5c-QA finding: "add Page 3" vanished from redo).
                protected_only = True
                break
            evicted_book += 1
            retained -= self._estimate_book_entry_bytes(victim, set())
            logger.debug(
                "Evicted book history entry seq=%d (%s) to honor the memory budget",
                victim.sequence,
                victim.label_key,
            )
        # Authoritative re-estimate: shared objects can make the running
        # subtraction drift, so the pass-end full estimate is the truth.
        retained = self._estimate_document_wide(set())
        over_budget = retained > budget
        if over_budget and protected_only:
            logger.warning(
                "Document history stays over its soft memory budget "
                "(retained=%d bytes, budget=%d bytes); the newest book "
                "undo recovery point and all redo custody are protected",
                retained,
                budget,
            )
        if over_budget and not work_remaining and not protected_only:
            # Drift left us over budget with candidates still available:
            # report unfinished work so callers loop / re-schedule.
            work_remaining = self._has_evictable_candidates()
        if not over_budget:
            # The authoritative estimate honors the budget: any deadline
            # break mid-drift was a false positive, nothing is left to do.
            work_remaining = False
        return BookHistoryTrimResult(
            evicted_entries=evicted_book,
            retained_bytes=retained,
            over_budget=over_budget,
            evicted_book_entries=evicted_book,
            evicted_content_groups=evicted_content,
            work_remaining=work_remaining,
        )

    def drop_redo_branch(self) -> None:
        """Invalidate redo after a structure change not yet command-backed.

        Phase H3 rule: direct ``duplicate_page`` / ``delete_page``
        mutations would make stale book redo entries unsafe, so the redo
        branch is discarded.  Existing undo entries stay; a revert that no
        longer applies fails loudly and keeps its entry (H4 integrates
        these operations properly).
        """
        if self._redo:
            logger.debug("Dropping %d book redo entries after direct change", len(self._redo))
        self._redo.clear()

    def clear(self, reason: str) -> None:
        """Drop everything at a document boundary (load / reset / new).

        Bumping the trim generation invalidates any callback scheduled
        before this boundary: when it fires, its generation no longer
        matches and it no-ops instead of trimming the new document's
        history (H4d2).  Save never calls this — history, bundles, and
        estimates survive a save.
        """
        had_entries = bool(self._undo or self._redo)
        if had_entries:
            logger.debug(
                "Dropping book history (%d undo / %d redo): %s",
                len(self._undo),
                len(self._redo),
                reason,
            )
        self._undo.clear()
        self._redo.clear()
        if had_entries:
            # Availability/counts changed; an empty clear stays silent.
            self._state_notifier.mark_changed()
        # Document boundary: the (possibly shared) sequence clock restarts
        # at 1.  PageServiceImpl clears content + book at the same
        # boundary, so a shared clock is reset exactly once semantically.
        self._sequence_clock.reset()
        self._trim_generation += 1


__all__ = ["BookHistoryEntry", "BookHistoryTrimResult", "PageCommandService"]
