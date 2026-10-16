"""Empty spread slot -> "insert a blank page here?" (CT-06 spec 06 §6.2).

The Pages panel never drags, so the empty slot itself is the affordance:
activating it asks for confirmation and, when the user agrees, inserts a
blank page through the existing ``add_page`` command path (one history
entry).  Reading order decides whether the new page goes before or after
the slot's real neighbour: the side read first in the current binding is
"before".  Inserting before the very first page is refused (the add path
only supports "after a page"), and the caller shows why.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

logger = logging.getLogger(__name__)

__all__ = ["EmptySlotInsertController"]

Position = Literal["before", "after"]


class EmptySlotInsertController:
    def __init__(self, page_service: Any, *, confirm: Callable[[str], bool]) -> None:
        self._pages = page_service
        self._confirm = confirm

    @staticmethod
    def position_for(empty_side: str, binding: str) -> Position:
        """Where a page dropped into the empty slot lands relative to its neighbour."""
        first_read = "right" if str(binding) == "right" else "left"
        return "before" if str(empty_side) == first_read else "after"

    def anchor_for(self, neighbor_page_id: str, position: Position) -> str | None:
        """Page id the blank page is inserted *after*; ``None`` when impossible."""
        if position == "after":
            return neighbor_page_id
        order = self._page_order()
        if neighbor_page_id not in order:
            return None
        index = order.index(neighbor_page_id)
        return order[index - 1] if index > 0 else None

    def insert(
        self, neighbor_page_id: str, empty_side: str, binding: str
    ) -> tuple[str | None, str]:
        """Returns ``(new_page_id | None, reason)``; reasons: ``inserted`` /
        ``cancelled`` / ``before_first_unsupported`` / ``failed``."""
        position = self.position_for(empty_side, binding)
        anchor = self.anchor_for(neighbor_page_id, position)
        if anchor is None:
            return None, "before_first_unsupported"
        if not self._confirm(position):
            return None, "cancelled"
        add_page = getattr(self._pages, "add_page", None)
        if not callable(add_page):
            return None, "failed"
        try:
            new_id = add_page(after_page_id=anchor)
        except Exception:
            logger.warning("blank page insert failed", exc_info=True)
            return None, "failed"
        return (str(new_id), "inserted") if new_id else (None, "failed")

    def _page_order(self) -> list[str]:
        list_pages = getattr(self._pages, "list_pages", None)
        if not callable(list_pages):
            return []
        try:
            return [str(summary.id) for summary in (list_pages() or [])]
        except Exception:
            return []
