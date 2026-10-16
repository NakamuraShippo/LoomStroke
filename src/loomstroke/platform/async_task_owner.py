"""Small asyncio task owner mixin for UI components."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from typing import Any, TypeVar, cast

T = TypeVar("T")


class AsyncTaskOwner:
    """Track and cancel asyncio tasks owned by a short-lived UI object."""

    def __init__(self) -> None:
        self._owned_async_tasks: set[asyncio.Task[Any]] = set()

    def create_owned_task(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        cancel_previous: asyncio.Task[Any] | None = None,
        consume_exceptions: bool = False,
        require_running: bool = False,
    ) -> asyncio.Task[T]:
        """Create and track a task, optionally replacing an older one."""
        if cancel_previous is not None:
            self.cancel_owned_task(cancel_previous)
        try:
            loop = asyncio.get_running_loop() if require_running else asyncio.get_event_loop()
        except RuntimeError:
            coro.close()
            raise
        task = loop.create_task(coro)
        return self.track_owned_task(task, consume_exceptions=consume_exceptions)

    def track_owned_task(
        self,
        task: asyncio.Task[T],
        *,
        consume_exceptions: bool = False,
    ) -> asyncio.Task[T]:
        """Track an externally-created task until it finishes."""
        tracked = cast(asyncio.Task[Any], task)
        self._owned_async_tasks.add(tracked)

        def _cleanup(finished: asyncio.Future[Any]) -> None:
            self._owned_async_tasks.discard(cast(asyncio.Task[Any], finished))
            if not consume_exceptions or finished.cancelled():
                return
            with contextlib.suppress(Exception):
                finished.exception()

        task.add_done_callback(_cleanup)
        return task

    def cancel_owned_task(self, task: asyncio.Task[Any]) -> None:
        """Cancel a single tracked task if it is still running."""
        self._owned_async_tasks.discard(task)
        if not task.done():
            task.cancel()

    def cancel_owned_tasks(self) -> None:
        """Cancel all tracked tasks without awaiting them."""
        for task in tuple(self._owned_async_tasks):
            if not task.done():
                task.cancel()
        self._owned_async_tasks.clear()

    def owned_task_count(self) -> int:
        """Return the number of still-tracked tasks."""
        return len(self._owned_async_tasks)
