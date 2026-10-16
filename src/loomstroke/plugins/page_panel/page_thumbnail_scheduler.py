"""Batched, visibility-first thumbnail requests for the Pages panel (CT-08 V3).

Wraps :class:`PageThumbnailQueue` with a zero-interval timer: the first
batch (the visible cards) is requested immediately so a freshly opened
document shows its saved thumbnails without a frame of delay; the rest
drain in small batches between events, keeping a 300-page replay from
blocking the UI thread in one burst (spec 08 §12).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from PySide6.QtCore import QTimer

from ...core.page_thumbnail_queue import PageThumbnailQueue

__all__ = ["PageThumbnailScheduler", "visible_page_ids"]


def visible_page_ids(list_widget: Any, item_map: Mapping[str, Any]) -> tuple[str, ...]:
    """Page ids whose cards intersect the list viewport (visual order)."""
    try:
        viewport_rect = list_widget.viewport().rect()
    except Exception:
        return ()
    visible: list[tuple[int, int, str]] = []
    for page_id, item in item_map.items():
        try:
            rect = list_widget.visualItemRect(item)
        except Exception:
            continue
        if rect.isValid() and rect.intersects(viewport_rect):
            visible.append((rect.top(), rect.left(), page_id))
    visible.sort()
    return tuple(page_id for _top, _left, page_id in visible)


class PageThumbnailScheduler:
    """Drain thumbnail requests in batches through ``request``."""

    def __init__(
        self,
        request: Callable[[str], None],
        *,
        batch_size: int = 8,
        timer_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._request = request
        self._batch_size = max(1, int(batch_size))
        self._queue = PageThumbnailQueue()
        self._timer = timer_factory() if timer_factory is not None else QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._drain_step)
        self.requests: list[str] = []

    @property
    def pending_count(self) -> int:
        return self._queue.pending_count

    def schedule(self, page_ids: Iterable[str], *, visible: Iterable[str] = ()) -> None:
        self._queue.schedule(page_ids, visible=visible)
        # Visible cards first and right away; the rest trickle in.
        self._request_batch()
        self._arm()

    def set_visible(self, page_ids: Iterable[str]) -> None:
        self._queue.set_visible(page_ids)

    def forget(self, page_id: str) -> None:
        self._queue.remove(page_id)

    def clear(self) -> None:
        self._queue.clear()
        self._timer.stop()

    def flush_now(self) -> None:
        """Drain everything synchronously (tests / shutdown)."""
        while self._queue.pending_count:
            self._request_batch()
        self._timer.stop()

    # -- internals ---------------------------------------------------------------

    def _arm(self) -> None:
        if self._queue.pending_count and not self._timer.isActive():
            self._timer.start()

    def _drain_step(self) -> None:
        self._request_batch()
        self._arm()

    def _request_batch(self) -> None:
        for page_id in self._queue.pop_batch(self._batch_size):
            self.requests.append(page_id)
            try:
                self._request(page_id)
            except Exception:
                continue
