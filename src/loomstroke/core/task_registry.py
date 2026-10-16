"""Task registry utilities for tool-scoped asyncio management."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ToolTaskRegistry:
    """Track asyncio tasks spawned by a tool session."""

    def __init__(self, owner: str) -> None:
        self._owner = owner
        self._tasks: set[asyncio.Task[Any]] = set()

    def create_task(self, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Create and track a task on the current event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        task: asyncio.Task[T] = loop.create_task(coro)
        self.track(task)
        return task

    def track(self, task: asyncio.Task[Any]) -> None:
        """Track an existing task for later cancellation."""
        self._tasks.add(task)

        def _cleanup(done_task: asyncio.Future[Any]) -> None:
            self._tasks.discard(done_task)

        task.add_done_callback(_cleanup)

    def pending_count(self) -> int:
        """Return the number of tasks that are still pending."""
        return sum(1 for task in self._tasks if not task.done())

    def cancel_all(self) -> None:
        """Cancel all tracked tasks without awaiting completion."""
        if not self._tasks:
            return
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()
        self._prune_done()

    async def cancel_all_async(self) -> None:
        """Cancel all tracked tasks and await completion."""
        if not self._tasks:
            return
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _prune_done(self) -> None:
        self._tasks = {task for task in self._tasks if not task.done()}
