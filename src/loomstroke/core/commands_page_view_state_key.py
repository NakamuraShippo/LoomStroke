"""Undoable patch of ONE key in a page's ``view_state`` (CT-14 §Command / Undo).

Other keys (rulers, guides, per-page viewport) are never rewritten; undo
restores the previous value of the key or removes it when it did not exist.
"""

from __future__ import annotations

import copy
from typing import Any

from .command_types import Command

__all__ = ["SetPageViewStateKeyCommand"]

_MISSING = object()


class SetPageViewStateKeyCommand(Command):
    def __init__(
        self,
        pages: Any,
        page_id: str,
        key: str,
        value: Any,
        *,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self._pages = pages
        self.page_id = page_id
        self.key = key
        self.value = copy.deepcopy(value)
        self._previous: Any = _MISSING

    def apply(self, canvas: Any) -> bool:
        current = self._current()
        if current is None and not callable(getattr(self._pages, "set_page_view_state", None)):
            return False
        self._previous = (
            copy.deepcopy(current[self.key]) if current and self.key in current else _MISSING
        )
        self._write(self.value)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        self._write(None if self._previous is _MISSING else self._previous)
        self._executed = False
        return True

    # -- internals ---------------------------------------------------------------------

    def _current(self) -> dict[str, Any] | None:
        getter = getattr(self._pages, "get_page_view_state", None)
        if not callable(getter):
            return None
        state = getter(self.page_id)
        return dict(state) if isinstance(state, dict) else {}

    def _write(self, value: Any) -> None:
        if value is None:
            merged = self._current() or {}
            merged.pop(self.key, None)
            setter = getattr(self._pages, "set_page_view_state", None)
            if callable(setter):
                setter(self.page_id, merged)
            return
        updater = getattr(self._pages, "update_page_view_state", None)
        if callable(updater):
            updater(self.page_id, {self.key: copy.deepcopy(value)}, mark_dirty=True)
            return
        merged = self._current() or {}
        merged[self.key] = copy.deepcopy(value)
        setter = getattr(self._pages, "set_page_view_state", None)
        if callable(setter):
            setter(self.page_id, merged)
