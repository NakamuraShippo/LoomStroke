"""Visibility-first thumbnail request queue (CT-08 V3, spec 08 §6.3 / §12).

Pure bookkeeping: which page thumbnails still need to be requested and
in what order.  Visible pages come first (in their visible order), then
the rest in the order they were scheduled; duplicates coalesce; a page
can be dropped (deleted) or re-prioritised (scrolled into view) at any
time.  Draining happens in small batches so a 300-page panel never asks
for everything in one synchronous burst.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["PageThumbnailQueue"]


class PageThumbnailQueue:
    """Ordered, de-duplicated set of pending thumbnail requests."""

    def __init__(self) -> None:
        self._pending: dict[str, None] = {}
        self._visible: tuple[str, ...] = ()

    # -- mutation ------------------------------------------------------------------

    def schedule(self, page_ids: Iterable[str], *, visible: Iterable[str] = ()) -> None:
        for page_id in page_ids:
            self._pending.setdefault(str(page_id), None)
        visible_ids = tuple(str(page_id) for page_id in visible)
        if visible_ids:
            self._visible = visible_ids

    def set_visible(self, page_ids: Iterable[str]) -> None:
        self._visible = tuple(str(page_id) for page_id in page_ids)

    def remove(self, page_id: str) -> None:
        self._pending.pop(page_id, None)

    def clear(self) -> None:
        self._pending.clear()

    def pop_batch(self, size: int) -> list[str]:
        """Next ``size`` page ids: visible ones first, then scheduling order."""
        if size < 1 or not self._pending:
            return []
        batch: list[str] = []
        for page_id in self._visible:
            if page_id in self._pending and page_id not in batch:
                batch.append(page_id)
                if len(batch) >= size:
                    break
        if len(batch) < size:
            for page_id in self._pending:
                if page_id not in batch:
                    batch.append(page_id)
                    if len(batch) >= size:
                        break
        for page_id in batch:
            self._pending.pop(page_id, None)
        return batch

    # -- queries -------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def is_pending(self, page_id: str) -> bool:
        return page_id in self._pending

    @property
    def visible(self) -> tuple[str, ...]:
        return self._visible
