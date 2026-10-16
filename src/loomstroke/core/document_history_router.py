"""Mixed-scope Undo/Redo routing core (CT-05 Phase H5a).

``DocumentHistoryRouter`` decides whether the next Undo/Redo targets the
*active* canvas content history or the document-wide book history, by
comparing only the two top candidates through non-mutating peek surfaces
and the shared document-local sequence (``DocumentHistorySequence``).

Scope of this router:

- it owns no stacks, no snapshots, no bundles, no memory budget, and no
  sequence-increment policy — dispatch goes through the existing
  ``canvas.history_undo()/history_redo()`` and
  ``PageCommandService.undo()/redo()`` entry points;
- CT-05 H6 (user-approved spec change, 2026-07-20): the document keeps
  ONE undo/redo timeline.  With a coordinator wired, inactive pages'
  content tops join the candidate pool; dispatching a candidate that
  lives on another page first activates that page (the change is always
  visible).  Undo = largest sequence, Redo = smallest sequence, across
  the whole document;
- the router never falls back to the other scope/page when the selected
  dispatch fails (a failure must stay visible and the sequence order
  must never be skipped).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

_CONTENT: Literal["content"] = "content"
_BOOK: Literal["book"] = "book"


@dataclass(frozen=True, slots=True)
class HistoryPageContext:
    """Pure page metadata for history UI labels (CT-05 H5c, internal).

    Holds only stable, untranslated values: the UI layer decides whether
    to show ``custom_name`` verbatim or a localized "Page {number}".
    Never persisted; never contains translated strings.
    """

    page_id: str
    custom_name: str | None
    one_based_index: int | None


@dataclass(frozen=True, slots=True)
class HistoryRouteCandidate:
    """Immutable metadata for one routable top entry (internal API).

    Labels are stable source values (content group label / book
    ``label_key``) — never translated UI strings, and page names are
    never baked in.  Building a candidate mutates nothing.
    """

    scope: Literal["content", "book"]
    sequence: int
    source_label: str
    label_key: str | None
    active_page_id: str | None
    affected_page_ids: tuple[str, ...]


class DocumentHistoryRouter:
    """Route Undo/Redo between active content history and book history."""

    def __init__(
        self,
        *,
        canvas: Any,
        book_history: Any,
        active_page_id_getter: Callable[[], str | None],
        coordinator: Any | None = None,
        page_activator: Callable[[str], None] | None = None,
    ) -> None:
        self._canvas = canvas
        self._book_history = book_history
        self._active_page_id_getter = active_page_id_getter
        # CT-05 H6 (document-wide single timeline): with a coordinator the
        # inactive pages' content tops join the candidate pool, and the
        # page activator lets dispatch jump to the owning page first.
        # Without them the pre-H6 active-content + book routing applies.
        self._coordinator = coordinator
        self._page_activator = page_activator

    # ------------------------------------------------------------------
    # Candidate peeks (non-mutating)
    def _content_candidate(self, *, redo: bool) -> HistoryRouteCandidate | None:
        peek_name = "_history_peek_redo_group" if redo else "_history_peek_undo_group"
        peek = getattr(self._canvas, peek_name, None)
        if not callable(peek):
            return None  # legacy/portless canvas: no content candidate surface
        group = peek()
        if group is None:
            return None
        return HistoryRouteCandidate(
            scope=_CONTENT,
            sequence=int(getattr(group, "sequence", 0)),
            source_label=str(getattr(group, "label", "")),
            label_key=None,
            # For content candidates this is the OWNING page (H6): the
            # active canvas belongs to the active page.
            active_page_id=self._active_page_id_getter(),
            affected_page_ids=(),
        )

    def _inactive_candidates(self, *, redo: bool) -> list[HistoryRouteCandidate]:
        """Content tops of inactive pages (H6 single-timeline pool).

        Unsequenced (legacy) groups are skipped — without a document
        sequence they cannot be ordered against other pages.
        """
        if self._coordinator is None:
            return []
        tops = getattr(self._coordinator, "iter_inactive_history_tops", None)
        if not callable(tops):
            return []
        candidates: list[HistoryRouteCandidate] = []
        for page_id, group in sorted(tops(redo=redo)):
            sequence = int(getattr(group, "sequence", 0))
            if sequence <= 0:
                continue
            candidates.append(
                HistoryRouteCandidate(
                    scope=_CONTENT,
                    sequence=sequence,
                    source_label=str(getattr(group, "label", "")),
                    label_key=None,
                    active_page_id=page_id,  # owning page, not the active one
                    affected_page_ids=(),
                )
            )
        return candidates

    def _book_candidate(self, *, redo: bool) -> HistoryRouteCandidate | None:
        entry = (
            self._book_history.peek_redo_entry() if redo else self._book_history.peek_undo_entry()
        )
        if entry is None:
            return None
        return HistoryRouteCandidate(
            scope=_BOOK,
            sequence=int(entry.sequence),
            source_label=str(entry.label_key),
            label_key=str(entry.label_key),
            active_page_id=self._active_page_id_getter(),
            affected_page_ids=tuple(entry.affected_page_ids),
        )

    @staticmethod
    def _select(
        content: HistoryRouteCandidate | None,
        book: HistoryRouteCandidate | None,
        *,
        prefer_smallest: bool,
    ) -> HistoryRouteCandidate | None:
        """Pick between the two top candidates.

        Undo prefers the largest sequence (newest first); Redo prefers
        the smallest (original chronological order).  Deterministic
        legacy rules: a lone candidate wins even when unsequenced; if
        only one side carries a positive sequence it wins; if both are
        unsequenced, content wins (pre-router canvas Undo behaviour).  A
        sequence tie cannot occur with one shared clock — if it does,
        content wins and the anomaly is logged.  Selection never claims
        a sequence and never mutates a stack.
        """
        if content is None or book is None:
            return content if book is None else book
        if content.sequence <= 0 or book.sequence <= 0:
            # Legacy/unsequenced: a lone positive sequence wins; both
            # unsequenced falls back to content (pre-router behaviour).
            return content if content.sequence > 0 or book.sequence <= 0 else book
        if content.sequence == book.sequence:
            logger.warning(
                "History sequence tie (%d) between content and book candidates; "
                "routing to content deterministically",
                content.sequence,
            )
            return content
        if prefer_smallest:
            return content if content.sequence < book.sequence else book
        return content if content.sequence > book.sequence else book

    def _pick(self, *, redo: bool) -> HistoryRouteCandidate | None:
        """Pick the next target over the whole document timeline (H6).

        Candidate pool: active content top, book top, and every inactive
        page's content top.  Undo takes the largest sequence, Redo the
        smallest.  When no candidate carries a sequence the legacy
        active-content/book pairwise rules apply (inactive tops without
        sequences are never routed).  Ties cannot occur under the shared
        clock; if one does, the pool order (active content, book,
        inactive by page id) wins deterministically with a warning.
        """
        content = self._content_candidate(redo=redo)
        book = self._book_candidate(redo=redo)
        pool: list[HistoryRouteCandidate] = []
        if content is not None:
            pool.append(content)
        if book is not None:
            pool.append(book)
        pool.extend(self._inactive_candidates(redo=redo))
        sequenced = [candidate for candidate in pool if candidate.sequence > 0]
        if not sequenced:
            return self._select(content, book, prefer_smallest=redo)
        chooser = min if redo else max
        best = chooser(sequenced, key=lambda candidate: candidate.sequence)
        if sum(1 for candidate in sequenced if candidate.sequence == best.sequence) > 1:
            logger.warning(
                "History sequence tie (%d) across timeline candidates; "
                "routing by pool order deterministically",
                best.sequence,
            )
            best = next(candidate for candidate in sequenced if candidate.sequence == best.sequence)
        return best

    def peek_undo(self) -> HistoryRouteCandidate | None:
        return self._pick(redo=False)

    def peek_redo(self) -> HistoryRouteCandidate | None:
        return self._pick(redo=True)

    # ------------------------------------------------------------------
    # State
    def can_undo(self) -> bool:
        return self.peek_undo() is not None

    def can_redo(self) -> bool:
        return self.peek_redo() is not None

    def _content_count(self, name: str) -> int:
        counter = getattr(self._canvas, name, None)
        return int(counter()) if callable(counter) else 0

    def _inactive_counts(self) -> tuple[int, int]:
        if self._coordinator is None:
            return (0, 0)
        counts = getattr(self._coordinator, "inactive_group_counts", None)
        if not callable(counts):
            return (0, 0)
        undo_total, redo_total = counts()
        return int(undo_total), int(redo_total)

    def undo_count(self) -> int:
        inactive_undo, _inactive_redo = self._inactive_counts()
        return (
            self._content_count("history_undo_count")
            + int(self._book_history.undo_count())
            + inactive_undo
        )

    def redo_count(self) -> int:
        _inactive_undo, inactive_redo = self._inactive_counts()
        return (
            self._content_count("history_redo_count")
            + int(self._book_history.redo_count())
            + inactive_redo
        )

    # ------------------------------------------------------------------
    # Dispatch (single scope; never falls back to the other one)
    def _activate_owning_page(self, candidate: HistoryRouteCandidate) -> bool:
        """Make the candidate's owning page active before a content dispatch.

        H6 single-timeline rule confirmed with the user: undo/redo jumps
        to the page where the change happened, so the change is always
        visible.  Page switches consume no sequence.  A failed jump
        reports False — the entry stays put and no other scope runs.
        """
        target = candidate.active_page_id
        current = self._active_page_id_getter()
        if not target or target == current:
            return True
        if self._page_activator is None:
            logger.warning("History timeline needs a page jump but no page activator is wired")
            return False
        self._page_activator(target)
        if self._active_page_id_getter() != target:
            logger.warning("History timeline page jump was refused; dispatch aborted")
            return False
        return True

    def _dispatch(self, candidate: HistoryRouteCandidate, *, redo: bool) -> bool:
        # CT-05 H5c: non-persistent DEBUG diagnostic, one line per real
        # dispatch attempt.  Deliberately excludes page names/ids, labels,
        # and payload data; computed only when DEBUG is enabled so the
        # normal path stays free of timing overhead.
        diagnostics = logger.isEnabledFor(logging.DEBUG)
        started = time.monotonic() if diagnostics else 0.0
        direction = "redo" if redo else "undo"
        try:
            if candidate.scope == _CONTENT:
                if self._activate_owning_page(candidate):
                    if redo:
                        result = bool(self._canvas.history_redo())
                    else:
                        result = bool(self._canvas.history_undo())
                else:
                    result = False  # jump failed: no fallback, entry stays
            elif redo:
                result = bool(self._book_history.redo())
            else:
                result = bool(self._book_history.undo())
        except Exception:
            # Same contract as the PageCommandService boundary: log the
            # failure and report False; the entry stays on its stack.
            # (The diagnostic line below reports result=failed without
            # duplicating this traceback.)
            logger.exception(
                "Document history %s dispatch failed (scope=%s, sequence=%d)",
                direction,
                candidate.scope,
                candidate.sequence,
            )
            result = False
        if diagnostics:
            logger.debug(
                "history-dispatch direction=%s scope=%s sequence=%d result=%s elapsed_ms=%.2f",
                direction,
                candidate.scope,
                candidate.sequence,
                "success" if result else "failed",
                (time.monotonic() - started) * 1000.0,
            )
        return result

    def undo(self) -> bool:
        """Undo the top candidate with the largest sequence.

        A failed dispatch returns False without trying the other scope:
        skipping the sequence order would silently reorder history, and
        a failing book command may indicate a broken document invariant.
        """
        candidate = self.peek_undo()
        if candidate is None:
            logger.debug("history-dispatch direction=undo result=no-candidate")
            return False
        return self._dispatch(candidate, redo=False)

    def redo(self) -> bool:
        """Redo the top candidate with the smallest sequence (no fallback)."""
        candidate = self.peek_redo()
        if candidate is None:
            logger.debug("history-dispatch direction=redo result=no-candidate")
            return False
        return self._dispatch(candidate, redo=True)


__all__ = ["DocumentHistoryRouter", "HistoryPageContext", "HistoryRouteCandidate"]
