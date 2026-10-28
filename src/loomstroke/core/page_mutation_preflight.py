"""Keep a page mutation on the same document and states across lazy reads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class PageMutationPreflight:
    """A short-lived ownership check, not another page or loader registry.

    Hydration may call application code. Check every original target again
    afterwards, including targets hydrated earlier in a multi-page preflight.
    Successful reads may populate their cache even when mutation is refused.
    """

    def __init__(self, service: Any, page_ids: Sequence[str]) -> None:
        self._service = service
        self._canvas = service._canvas
        self._document = getattr(self._canvas, "_doc", None)
        self._pages = service._pages
        current = {state.page.id: state for state in self._pages}
        self.targets = tuple((page_id, current.get(page_id)) for page_id in page_ids)

    def is_current(self) -> bool:
        if (
            self._service._canvas is not self._canvas
            or getattr(self._canvas, "_doc", None) is not self._document
            or self._service._pages is not self._pages
        ):
            return False
        # One local map per check avoids one linear page lookup per target.
        current = {state.page.id: state for state in self._pages}
        return all(
            state is not None and current.get(page_id) is state for page_id, state in self.targets
        )

    def is_ready(self) -> bool:
        """A later loader may evict an earlier target without changing its state."""
        return self.is_current() and all(state.hydrated for _page_id, state in self.targets)

    def hydrate(self) -> bool:
        if not self.is_current():
            return False
        for _page_id, state in self.targets:
            if not self._service._ensure_page_hydrated(state) or not self.is_current():
                return False
        return self.is_ready()
