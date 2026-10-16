"""
Qt/asyncio integration with qasync.

Per Master.md PR#4 specifications, provides complete Qt/asyncio
unification for non-blocking UI operations.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import partial, wraps
from typing import (
    Any,
    ParamSpec,
    TypeVar,
    cast,
)

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

try:
    from qasync import QEventLoop, QThreadExecutor
    from qasync import asyncSlot as _qasync_async_slot
except ImportError:
    raise ImportError("qasync is required. Install with: pip install qasync")  # noqa: B904

logger = logging.getLogger(__name__)


T = TypeVar("T")
R = TypeVar("R")
P = ParamSpec("P")


def async_slot(
    *decorator_args: Any, **decorator_kwargs: Any
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Typed wrapper around qasync.asyncSlot to satisfy mypy."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        wrapped = _qasync_async_slot(*decorator_args, **decorator_kwargs)(func)
        return cast(Callable[P, Awaitable[R]], wrapped)

    return decorator


class QtAsyncIntegration:
    """
    Manages Qt/asyncio integration with qasync.

    Ensures single event loop, proper cleanup, and thread safety.
    """

    _instance: QtAsyncIntegration | None = None
    _loop: QEventLoop | None = None

    def __new__(cls) -> QtAsyncIntegration:
        """Ensure singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize integration (only once)."""
        if not hasattr(self, "_initialized"):
            self._initialized = True
            self._app: QApplication | None = None
            self._executor: QThreadExecutor | None = None
            self._tasks: set[asyncio.Task[Any]] = set()
            logger.info("QtAsyncIntegration initialized")

    def setup(self, app: QApplication) -> QEventLoop:
        """
        Set up qasync event loop.

        Args:
            app: Qt application instance

        Returns:
            Configured QEventLoop
        """
        if self._loop is not None:
            logger.warning("Event loop already set up")
            return self._loop

        self._app = app

        # Create and set the event loop
        self._loop = QEventLoop(app)
        asyncio.set_event_loop(self._loop)

        # Create thread executor for blocking operations
        self._executor = QThreadExecutor(max_workers=4)

        logger.info("QEventLoop configured successfully")
        return self._loop

    @property
    def loop(self) -> QEventLoop | None:
        """Get the current event loop."""
        return self._loop

    @property
    def executor(self) -> QThreadExecutor | None:
        """Get the thread executor."""
        return self._executor

    async def run_in_executor(self, func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Run blocking function in the thread executor."""
        if self._executor is None:
            raise RuntimeError("Executor not initialized")

        loop = asyncio.get_running_loop()
        bound = partial(func, *args, **kwargs)
        return await loop.run_in_executor(self._executor, bound)

    def create_task(self, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Create and track a coroutine task bound to the Qt event loop."""
        if self._loop is None:
            raise RuntimeError("Event loop not initialized")

        task: asyncio.Task[T] = self._loop.create_task(coro)

        def _cleanup(finished: asyncio.Future[Any]) -> None:
            self._tasks.discard(cast(asyncio.Task[Any], finished))

        self._tasks.add(cast(asyncio.Task[Any], task))
        task.add_done_callback(_cleanup)
        return task

    async def cancel_all_tasks(self) -> None:
        """Cancel all tracked tasks."""
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Wait for cancellation
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        logger.info(f"Cancelled {len(self._tasks)} tasks")
        self._tasks.clear()

    async def shutdown(self) -> None:
        """Shutdown integration and cleanup."""
        logger.info("Shutting down QtAsyncIntegration...")

        # Cancel all tasks
        await self.cancel_all_tasks()

        # Shutdown executor
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None

        # Clear loop reference
        self._loop = None

        logger.info("QtAsyncIntegration shutdown complete")


class AsyncQObject(QObject):
    """
    Base class for QObjects with async support.

    Provides async signal/slot integration and lifecycle management.
    """

    # Async lifecycle signals
    async_ready = Signal()
    async_error = Signal(str)

    def __init__(self, parent: QObject | None = None):
        """Initialize async QObject."""
        super().__init__(parent)
        self._async_tasks: set[asyncio.Task[Any]] = set()

    @async_slot()
    async def async_initialize(self) -> None:
        """
        Async initialization hook.

        Override in subclasses for async setup.
        """
        pass

    @async_slot()
    async def async_cleanup(self) -> None:
        """
        Async cleanup hook.

        Override in subclasses for async teardown.
        """
        # Cancel all tasks
        for task in self._async_tasks:
            if not task.done():
                task.cancel()

        if self._async_tasks:
            await asyncio.gather(*self._async_tasks, return_exceptions=True)

        self._async_tasks.clear()

    def create_async_task(self, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Create and track an async task owned by this QObject."""
        task: asyncio.Task[T] = asyncio.create_task(coro)

        def _cleanup(finished: asyncio.Future[Any]) -> None:
            self._async_tasks.discard(cast(asyncio.Task[Any], finished))

        self._async_tasks.add(cast(asyncio.Task[Any], task))
        task.add_done_callback(_cleanup)
        return task

    @async_slot(str)
    async def handle_async_error(self, error: str) -> None:
        """
        Handle async errors.

        Args:
            error: Error message
        """
        logger.error(f"Async error in {self.__class__.__name__}: {error}")
        self.async_error.emit(error)


def ensure_async_slot(func: Callable[P, Awaitable[T] | T]) -> Callable[P, Awaitable[T]]:
    """Ensure the given callable is exposed as an async slot."""
    if asyncio.iscoroutinefunction(func):
        return async_slot()(cast(Callable[P, Awaitable[T]], func))

    @wraps(func)
    async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        return cast(T, func(*args, **kwargs))

    return async_slot()(async_wrapper)


@asynccontextmanager
async def async_timeout(seconds: float) -> AsyncIterator[None]:
    """
    Async context manager for timeout.

    Args:
        seconds: Timeout in seconds

    Yields:
        None

    Raises:
        asyncio.TimeoutError: If timeout occurs
    """
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError:
        logger.warning(f"Operation timed out after {seconds} seconds")
        raise


# Global instance
_integration: QtAsyncIntegration | None = None


def get_qt_async_integration() -> QtAsyncIntegration:
    """
    Get global QtAsyncIntegration instance.

    Returns:
        QtAsyncIntegration singleton
    """
    global _integration  # noqa: PLW0603
    if _integration is None:
        _integration = QtAsyncIntegration()
    return _integration
