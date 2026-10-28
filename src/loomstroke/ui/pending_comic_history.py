"""Keep history keys ordered behind an asynchronously prepared comic edit."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from .history_failure_feedback import request_history
from .pending_comic_shortcuts import PendingComicShortcutFilter

logger = logging.getLogger(__name__)


class PendingComicHistory:
    """Queue UI history requests until the pending edit has actually committed."""

    def __init__(self, window: Any, refresh: Any) -> None:
        self._window = window
        self._refresh = refresh
        self._task: Any = None
        self._requests: list[str] = []
        self._shortcut_filter = (
            PendingComicShortcutFilter(window, self) if isinstance(window, QWidget) else None
        )
        # EventBus uses weak references; retain the bound handler.
        self._handler = self._preparing
        subscribe = getattr(getattr(window, "_event_bus", None), "on", None)
        if callable(subscribe):
            subscribe("comic.panel.preparing", self._handler)

    @property
    def active(self) -> bool:
        return self._task is not None

    @property
    def can_redo(self) -> bool:
        return self._requests.count("undo") > self._requests.count("redo")

    def defer(self, direction: str) -> bool:
        if not self.active:
            return False
        if direction == "redo" and not self.can_redo:
            return True
        self._requests.append(direction)
        self._refresh()
        return True

    def _preparing(self, payload: Any) -> None:
        host = getattr(self._window, "_host_api", None)
        if (
            host is None
            or not isinstance(payload, dict)
            or payload.get("canvas") is not getattr(host, "canvas", None)
        ):
            return
        task = payload.get("task")
        if task is None or task is self._task:
            return
        self._task = task
        self._requests = []
        task.add_done_callback(self._completed)
        self._refresh()

    def _completed(self, task: Any) -> None:
        if task is not self._task:
            return
        requests, self._requests = self._requests, []
        self._task = None
        if isinstance(self._window, QWidget) and not isValid(self._window):
            return
        try:
            committed = not task.cancelled() and task.result() is True
            if committed:
                history = self._window._host_api.history
                for direction in requests:
                    if not request_history(self._window, history, direction):
                        break
        except Exception:
            logger.exception("Deferred comic history request failed")
        finally:
            # A cancelled/replaced/failed preparation must never undo an older
            # operation just because the requested edit did not reach history.
            self._refresh()
