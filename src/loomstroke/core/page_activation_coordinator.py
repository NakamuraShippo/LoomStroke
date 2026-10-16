"""Page switch bookkeeping: coalescing, failure memory, prefetch hints (CT-08 V3, §6.2).

Pure state machine used by the Pages panel controller: rapid requests
collapse to the latest one (``request`` / ``begin``), a failed activation
is remembered per page so the card can offer a retry, and
``prefetch_candidates`` names the neighbours worth warming after a
switch.  No Qt, no page service.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["PageActivationCoordinator"]


class PageActivationCoordinator:
    def __init__(self) -> None:
        self._pending: str | None = None
        self._loading: str | None = None
        self._failed: dict[str, str] = {}

    # -- requests ------------------------------------------------------------------

    def request(self, page_id: str) -> bool:
        """Record a request; returns ``True`` when nothing was pending before."""
        was_idle = self._pending is None
        self._pending = str(page_id)
        return was_idle

    @property
    def pending(self) -> str | None:
        return self._pending

    def begin(self) -> str | None:
        """Take the latest pending request and mark it loading."""
        page_id, self._pending = self._pending, None
        if page_id is not None:
            self._loading = page_id
        return page_id

    def complete(self, page_id: str, *, ok: bool, reason: str = "") -> None:
        if self._loading == page_id:
            self._loading = None
        if ok:
            self._failed.pop(page_id, None)
        else:
            self._failed[page_id] = reason or "activation_failed"

    # -- state ---------------------------------------------------------------------

    @property
    def loading(self) -> str | None:
        return self._loading

    def is_loading(self, page_id: str) -> bool:
        return self._loading == page_id

    def has_failed(self, page_id: str) -> bool:
        return page_id in self._failed

    def failure_reason(self, page_id: str) -> str | None:
        return self._failed.get(page_id)

    def clear_failure(self, page_id: str) -> None:
        self._failed.pop(page_id, None)

    @property
    def failed_pages(self) -> tuple[str, ...]:
        return tuple(self._failed)

    def forget(self, page_id: str) -> None:
        """Page deleted: drop every trace of it."""
        if self._pending == page_id:
            self._pending = None
        if self._loading == page_id:
            self._loading = None
        self._failed.pop(page_id, None)

    # -- prefetch ------------------------------------------------------------------

    @staticmethod
    def prefetch_candidates(
        page_ids: Sequence[str], active: str | None, *, radius: int = 1
    ) -> tuple[str, ...]:
        """Neighbours of ``active`` in logical order, nearest first."""
        if active is None or active not in page_ids or radius < 1:
            return ()
        index = list(page_ids).index(active)
        candidates: list[str] = []
        for distance in range(1, radius + 1):
            for position in (index + distance, index - distance):
                if 0 <= position < len(page_ids):
                    candidates.append(page_ids[position])
        return tuple(candidates)
