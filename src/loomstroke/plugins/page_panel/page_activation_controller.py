"""Pages panel -> PageService activation with failure memory and prefetch (CT-08 V3).

The panel hands every click / keyboard selection here.  Activation stays
synchronous (the service's contract), but the controller records when
the switch was refused (V2c: the service stays on the current page), so
the card can show a retry hint, and after a successful switch it warms
the neighbouring pages' content on the next event-loop turn (spec 08
§6.1 prefetch) — bounded by the service's own clean-page budget.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QTimer

from ...core.page_activation_coordinator import PageActivationCoordinator

logger = logging.getLogger(__name__)

__all__ = ["PageActivationController"]


class PageActivationController:
    def __init__(
        self,
        page_service: Any,
        *,
        on_failed: Callable[[str], None] | None = None,
        on_recovered: Callable[[str], None] | None = None,
        prefetch: bool = True,
        timer_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._pages = page_service
        self._on_failed = on_failed
        self._on_recovered = on_recovered
        self._prefetch_enabled = prefetch
        self._coordinator = PageActivationCoordinator()
        self._prefetch_ids: tuple[str, ...] = ()
        self._timer = timer_factory() if timer_factory is not None else QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._run_prefetch)

    @property
    def coordinator(self) -> PageActivationCoordinator:
        return self._coordinator

    def activate(self, page_id: str) -> bool:
        """Switch to ``page_id``; returns whether the service now shows it."""
        self._coordinator.request(page_id)
        target = self._coordinator.begin()
        if target is None:
            return False
        failed_before = self._coordinator.has_failed(target)
        active_before = self._active_id()
        try:
            self._pages.set_active_page(target)
        except Exception as error:
            logger.debug("activation raised for %s", target, exc_info=True)
            self._coordinator.complete(target, ok=False, reason=type(error).__name__)
            self._notify_failed(target)
            return False
        ok = self._active_id() == target or target == active_before
        self._coordinator.complete(target, ok=ok, reason="" if ok else "activation_refused")
        if not ok:
            self._notify_failed(target)
            return False
        if failed_before and self._on_recovered is not None:
            self._on_recovered(target)
        self._schedule_prefetch(target)
        return True

    def has_failed(self, page_id: str) -> bool:
        return self._coordinator.has_failed(page_id)

    def forget(self, page_id: str) -> None:
        self._coordinator.forget(page_id)

    def flush_prefetch(self) -> None:
        self._timer.stop()
        self._run_prefetch()

    # -- internals ---------------------------------------------------------------

    def _notify_failed(self, page_id: str) -> None:
        if self._on_failed is not None:
            self._on_failed(page_id)

    def _active_id(self) -> str | None:
        getter = getattr(self._pages, "get_active_page_id", None)
        try:
            return str(getter()) if callable(getter) else None
        except Exception:
            return None

    def _page_order(self) -> Sequence[str]:
        list_pages = getattr(self._pages, "list_pages", None)
        if not callable(list_pages):
            return []
        try:
            return [str(summary.id) for summary in (list_pages() or [])]
        except Exception:
            return []

    def _schedule_prefetch(self, active: str) -> None:
        if not self._prefetch_enabled:
            return
        self._prefetch_ids = PageActivationCoordinator.prefetch_candidates(
            self._page_order(), active, radius=1
        )
        if self._prefetch_ids and not self._timer.isActive():
            self._timer.start()

    def _run_prefetch(self) -> None:
        ids, self._prefetch_ids = self._prefetch_ids, ()
        snapshot = getattr(self._pages, "get_page_snapshot", None)
        evict = getattr(self._pages, "evict_pages_to_budget", None)
        if not callable(snapshot):
            return
        for page_id in ids:
            try:
                snapshot(page_id)  # hydrates a placeholder; budget evicts later
            except Exception:
                logger.debug("prefetch failed for %s", page_id, exc_info=True)
        if callable(evict):
            try:
                evict()
            except Exception:
                logger.debug("prefetch evict failed", exc_info=True)
