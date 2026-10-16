"""Runtime-only per-page history bundle ownership (CT-05 Phase H2).

``PageHistoryCoordinator`` keeps the detached undo/redo bundles of
inactive pages so that switching pages retains each page's content
history for the session.  Bundles are runtime-only: they are never
persisted to ``.lstroke``, autosave, or page metadata, and every
document boundary (load / reset / new document) drops them all.

Ownership is exclusive at all times: the canvas owns whatever stacks are
currently attached; this coordinator owns exactly the bundles of pages
that are not active.  Canvases without the history port (legacy
``Canvas``) are detected via ``supports_page_history()`` and keep the
pre-H2 clear-on-switch behaviour in ``PageServiceImpl``.

CT-05 H4d2 additions: every stored bundle carries a runtime-only
monotonic ordinal (its last-inactive age) so the document-wide memory
budget in ``PageCommandService`` can evict from the least-recently-active
page first.  Empty bundles are never stored — re-visiting such a page
starts with the same empty history as a first visit — so the retained
estimate covers real history only.
"""

from __future__ import annotations

import logging
from typing import Any

from loomstroke.core.canvas_history_entries import _HistoryGroup
from loomstroke.core.canvas_history_port import PageHistoryBundle
from loomstroke.core.page_history_memory import estimate_page_history_bundle_bytes

logger = logging.getLogger(__name__)


class PageHistoryCoordinator:
    """Own runtime ``PageHistoryBundle`` objects keyed by page id."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas
        self._bundles: dict[str, PageHistoryBundle] = {}
        # Runtime-only inactive age: page id -> monotonic ordinal assigned
        # whenever this coordinator takes custody of a non-empty bundle.
        # Never wall-clock, never persisted (CT-05 H4d2).
        self._ordinals: dict[str, int] = {}
        self._next_ordinal = 0

    def supports_page_history(self) -> bool:
        """Return True when the canvas exposes the H1 detach/attach port."""
        return callable(getattr(self._canvas, "_history_detach_bundle", None)) and callable(
            getattr(self._canvas, "_history_attach_bundle", None)
        )

    def _store_bundle(self, bundle: PageHistoryBundle) -> bool:
        """Take custody of ``bundle`` unless it is empty.

        Empty bundles are discarded instead of stored: re-activating the
        page then starts with the same empty history as a first visit,
        and the memory-budget bookkeeping never carries dead entries.
        """
        if not bundle.undo_groups and not bundle.redo_groups:
            return False
        self._bundles[bundle.page_id] = bundle
        self._ordinals[bundle.page_id] = self._next_ordinal
        self._next_ordinal += 1
        return True

    def detach_active_history(self, page_id: str | None, *, keep: bool) -> None:
        """Move the canvas stacks of the outgoing page into this coordinator.

        The H1 port commits any pending transaction first, so no recorded
        edit is lost.  With ``keep=False`` (page deleted or unknown) the
        detached bundle is discarded so its history cannot leak into the
        next page.
        """
        if not self.supports_page_history():
            return
        bundle = self._canvas._history_detach_bundle(str(page_id) if page_id else "")
        if keep and page_id:
            self._store_bundle(bundle)

    def attach_history_for(self, page_id: str) -> None:
        """Attach the stored bundle for ``page_id``; first visits stay empty.

        A bundle whose ``page_id`` label does not match is quarantined
        (dropped with a warning) instead of being attached to the wrong
        page; the page then opens with empty history.  If the canvas
        refuses the attach, ownership — bundle and original ordinal —
        returns to this coordinator before the error propagates.
        """
        if not self.supports_page_history():
            return
        bundle = self._bundles.pop(page_id, None)
        ordinal = self._ordinals.pop(page_id, None)
        if bundle is None:
            return
        if bundle.page_id != page_id:
            logger.warning(
                "Quarantining page history bundle labelled %r stored under page %r; "
                "opening the page with empty history",
                bundle.page_id,
                page_id,
            )
            return
        try:
            self._canvas._history_attach_bundle(bundle)
        except Exception:
            self._bundles[page_id] = bundle
            if ordinal is not None:
                self._ordinals[page_id] = ordinal
            raise

    def drop_page(self, page_id: str) -> None:
        """Discard the stored bundle of a deleted page."""
        self._bundles.pop(page_id, None)
        self._ordinals.pop(page_id, None)

    def take_bundle(self, page_id: str, *, is_active: bool) -> PageHistoryBundle | None:
        """Move a page's bundle out for ``DeletePageCommand`` custody.

        For the active page the bundle is detached from the canvas (the H1
        port commits any pending transaction first); for inactive pages the
        stored bundle is popped.  Either way this is an O(1) ownership
        transfer — after it, neither the canvas nor this coordinator holds
        the bundle.  Returns ``None`` for portless canvases and for pages
        that never accumulated history (empty bundles are not retained).
        """
        if not self.supports_page_history():
            return None
        if is_active:
            bundle = self._canvas._history_detach_bundle(str(page_id))
            if not bundle.undo_groups and not bundle.redo_groups:
                return None
            return bundle  # type: ignore[no-any-return]
        self._ordinals.pop(page_id, None)
        return self._bundles.pop(page_id, None)

    def restore_bundle(self, bundle: PageHistoryBundle) -> bool:
        """Take custody of a bundle again (delete-undo hand-back).

        The bundle is stored under its own ``page_id`` with a fresh
        inactive ordinal; re-activating the page attaches it through the
        normal page-switch path.  An empty bundle is accepted but
        discarded (equivalent to a first visit).  A bundle whose page
        already has one stored is refused loudly so two owners can never
        exist.
        """
        if not self.supports_page_history():
            return False
        if bundle.page_id in self._bundles:
            logger.warning(
                "Refusing to restore page history bundle %r: a bundle for that "
                "page is already stored",
                bundle.page_id,
            )
            return False
        self._store_bundle(bundle)
        return True

    # ------------------------------------------------------------------
    # Document-wide memory budget surface (CT-05 H4d2).  Consumed by
    # ``PageCommandService`` — the single budget-policy authority.
    def estimated_retained_bytes(self, seen: set[int]) -> int:
        """Approximate bytes retained by all stored inactive bundles.

        The caller shares one ``seen`` set with the book-history estimate
        so objects that migrate between owners (delete apply/undo) are
        never double counted.
        """
        total = 0
        for bundle in self._bundles.values():
            total += estimate_page_history_bundle_bytes(bundle, seen=seen)
        return total

    def has_evictable_groups(self) -> bool:
        """Return True when at least one inactive UNDO group remains.

        CT-05 H6: redo branches are self-limiting (any new operation
        drops them document-wide), so the memory trim never touches
        them — only undo groups are evictable.
        """
        return any(bundle.undo_groups for bundle in self._bundles.values())

    # ------------------------------------------------------------------
    # Global single-timeline surface (CT-05 H6).  Read-only peeks feed the
    # DocumentHistoryRouter's document-wide candidate selection; the redo
    # drop implements the "one redoable future" rule.
    def iter_inactive_history_tops(self, *, redo: bool) -> list[tuple[str, _HistoryGroup]]:
        """Return ``(page_id, top_group)`` for every stored bundle side.

        The top mirrors the canvas convention: ``[-1]`` is the next
        undo/redo of that page.  Non-mutating; empty sides are skipped.
        """
        tops: list[tuple[str, _HistoryGroup]] = []
        for page_id, bundle in self._bundles.items():
            stack = bundle.redo_groups if redo else bundle.undo_groups
            if stack:
                tops.append((page_id, stack[-1]))
        return tops

    def inactive_group_counts(self) -> tuple[int, int]:
        """Return ``(undo_groups, redo_groups)`` totals across bundles."""
        undo_total = 0
        redo_total = 0
        for bundle in self._bundles.values():
            undo_total += len(bundle.undo_groups)
            redo_total += len(bundle.redo_groups)
        return undo_total, redo_total

    def drop_all_redo_groups(self) -> int:
        """Discard every stored redo branch (new-operation rule, H6).

        A document-wide single timeline has exactly one redoable future:
        any new operation invalidates the redo branches of *all* pages.
        Bundles drained to empty are removed with their ordinals.
        Returns the number of groups dropped.
        """
        dropped = 0
        for page_id in list(self._bundles):
            bundle = self._bundles[page_id]
            dropped += len(bundle.redo_groups)
            bundle.redo_groups.clear()
            if not bundle.undo_groups:
                self._bundles.pop(page_id, None)
                self._ordinals.pop(page_id, None)
        return dropped

    def evict_oldest_inactive_group(self) -> _HistoryGroup | None:
        """Evict one UNDO group from the least-recently-active page.

        Order: least-recently-active page first, oldest undo group first
        (``undo_groups.popleft()``).  CT-05 H6: redo groups are NEVER
        evicted — the single-timeline rule drops every redo branch on
        the next new operation, so redo custody is transient by
        construction and eviction would silently destroy the user's
        only redoable future (H5c-QA finding).  A bundle whose undo side
        drains but still holds redo groups is kept; only a fully empty
        bundle is removed with its ordinal.  Returns the evicted group,
        or ``None`` when no undo group is evictable.
        """
        for page_id in sorted(self._ordinals, key=self._ordinals.__getitem__):
            bundle = self._bundles.get(page_id)
            if bundle is None:
                # Defensive: an ordinal without a bundle is stale bookkeeping.
                del self._ordinals[page_id]
                continue
            if not bundle.undo_groups:
                if not bundle.redo_groups:
                    self._bundles.pop(page_id, None)
                    self._ordinals.pop(page_id, None)
                continue  # redo-only bundles are protected
            group = bundle.undo_groups.popleft()
            if not bundle.undo_groups and not bundle.redo_groups:
                self._bundles.pop(page_id, None)
                self._ordinals.pop(page_id, None)
            logger.debug(
                "Evicted one content history group of inactive page %s "
                "to honor the document-wide memory budget",
                page_id,
            )
            return group
        return None

    def clear_all(self, reason: str) -> None:
        """Drop every stored bundle at a document boundary."""
        if self._bundles:
            logger.debug("Dropping %d page history bundles (%s)", len(self._bundles), reason)
        self._bundles.clear()
        self._ordinals.clear()
        self._next_ordinal = 0


__all__ = ["PageHistoryCoordinator"]
