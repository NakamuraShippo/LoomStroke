"""
Event Bus implementation for LoomStroke.

Per Master.md PR-B specifications, provides a thread-safe Pub/Sub system
for decoupling UI and plugin communication.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import threading
import weakref
from collections import defaultdict
from collections.abc import Awaitable as AwaitableABC
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, cast
from weakref import ReferenceType

logger = logging.getLogger(__name__)

# Event handler type
MaybeAwaitable = AwaitableABC[Any] | object
EventHandler = Callable[[Any], MaybeAwaitable]
HandlerRef = ReferenceType[EventHandler]


@dataclass(frozen=True)
class EventToken:
    """Token for event subscription management."""

    event: str
    handler_id: int

    def __hash__(self) -> int:
        return hash((self.event, self.handler_id))


class EventBus:
    """
    Thread-safe event bus for application-wide communication.

    This implements a simple Pub/Sub pattern per PR-B specifications.
    Handlers are stored as weak references to prevent memory leaks.
    """

    def __init__(self) -> None:
        """Initialize the event bus."""
        self._handlers: dict[str, list[HandlerRef]] = defaultdict(list)
        self._lock = threading.RLock()
        self._handler_counter = 0
        self._tokens: dict[EventToken, HandlerRef] = {}
        # Keep strong references for once() handlers
        self._once_handlers: dict[EventToken, tuple[EventHandler, EventHandler]] = {}

    def on(self, event: str, handler: EventHandler) -> EventToken:
        """
        Subscribe to an event.

        Args:
            event: Event name to subscribe to
            handler: Callback function to invoke when event fires

        Returns:
            Token that can be used to unsubscribe
        """
        with self._lock:
            # Create weak reference to handler
            handler_ref = cast(HandlerRef, weakref.ref(handler, self._cleanup_handler))

            # Add to handlers list
            self._handlers[event].append(handler_ref)

            # Create token for unsubscription
            self._handler_counter += 1
            token = EventToken(event, self._handler_counter)
            self._tokens[token] = handler_ref

            logger.debug(f"Subscribed to '{event}' (token: {token.handler_id})")
            return token

    def off(self, event: str, token: EventToken) -> None:
        """
        Unsubscribe from an event.

        Args:
            event: Event name to unsubscribe from
            token: Token returned from on() method
        """
        with self._lock:
            if token not in self._tokens:
                logger.warning(f"Token {token.handler_id} not found for '{event}'")
                return

            handler_ref = self._tokens.pop(token, None)
            if handler_ref and event in self._handlers:
                with suppress(ValueError):
                    self._handlers[event].remove(handler_ref)
                    logger.debug(f"Unsubscribed from '{event}' (token: {token.handler_id})")
                    if not self._handlers[event]:
                        del self._handlers[event]

            # Clean up once handler references if they exist
            self._once_handlers.pop(token, None)

    def emit(self, event: str, payload: Any = None) -> None:
        """
        Emit an event to all subscribers.

        Args:
            event: Event name to emit
            payload: Optional data to pass to handlers

        Note:
            Handler exceptions are logged but not re-raised to ensure
            all handlers are called.
        """
        with self._lock:
            handlers = self._handlers.get(event, []).copy()

        if not handlers:
            logger.debug(f"No handlers for event '{event}'")
            return

        logger.debug(f"Emitting '{event}' to {len(handlers)} handler(s)")

        # Call handlers outside lock to prevent deadlocks
        dead_refs: list[HandlerRef] = []
        for handler_ref in handlers:
            handler = handler_ref()
            if handler is None:
                dead_refs.append(handler_ref)
                continue

            try:
                result = handler(payload)
            except Exception as e:
                logger.error(
                    f"Handler error for '{event}': {e}",
                    exc_info=True,
                )
                continue

            if isinstance(result, AwaitableABC):
                logger.warning(
                    "Async handler detected for event '%s' via sync emit; "
                    "use emit_async to await handlers",
                    event,
                )
                self._schedule_async_result(event, result)

        # Clean up dead references
        if dead_refs:
            with self._lock:
                for ref in dead_refs:
                    with suppress(ValueError, KeyError):
                        self._handlers[event].remove(ref)

    def _cleanup_handler(self, ref: HandlerRef) -> None:
        """
        Clean up dead handler references.

        Called automatically when a handler is garbage collected.
        """
        with self._lock:
            # Remove from all event lists
            for event, handlers in list(self._handlers.items()):
                with suppress(ValueError):
                    handlers.remove(ref)
                    if not handlers:
                        del self._handlers[event]

            # Remove associated tokens
            tokens_to_remove = [
                token for token, handler_ref in self._tokens.items() if handler_ref == ref
            ]
            for token in tokens_to_remove:
                del self._tokens[token]

    def clear(self, event: str | None = None) -> None:
        """
        Clear handlers for specific event or all events.

        Args:
            event: Event name to clear, or None to clear all
        """
        with self._lock:
            if event is None:
                self._handlers.clear()
                self._tokens.clear()
                self._once_handlers.clear()
                logger.debug("Cleared all event handlers")
            elif event in self._handlers:
                # Find and remove associated tokens
                tokens_to_remove = [token for token in self._tokens if token.event == event]
                for token in tokens_to_remove:
                    del self._tokens[token]
                    self._once_handlers.pop(token, None)

                del self._handlers[event]
                logger.debug(f"Cleared handlers for '{event}'")

    def has_handlers(self, event: str) -> bool:
        """
        Check if an event has any handlers.

        Args:
            event: Event name to check

        Returns:
            True if event has handlers
        """
        with self._lock:
            return bool(self._handlers.get(event))

    def get_handler_count(self, event: str | None = None) -> int:
        """
        Get number of handlers for an event.

        Args:
            event: Event name, or None for total count

        Returns:
            Number of handlers
        """
        with self._lock:
            if event is None:
                return sum(len(handlers) for handlers in self._handlers.values())
            return len(self._handlers.get(event, []))

    # --- New API aliases for compatibility ---

    def subscribe(self, event: str, handler: EventHandler) -> EventToken:
        """
        Subscribe to an event (alias for 'on').

        Args:
            event: Event name to subscribe to
            handler: Callback function to invoke when event fires

        Returns:
            Token that can be used to unsubscribe
        """
        return self.on(event, handler)

    def publish(self, event: str, payload: Any = None) -> None:
        """
        Publish an event to all subscribers (alias for 'emit').

        Args:
            event: Event name to publish
            payload: Optional data to pass to handlers
        """
        self.emit(event, payload)

    def once(self, event: str, handler: EventHandler) -> EventToken:
        """
        Subscribe to an event for one-time execution only.

        The handler will be automatically unsubscribed after first invocation.

        Args:
            event: Event name to subscribe to
            handler: Callback function to invoke once when event fires

        Returns:
            Token that can be used to unsubscribe before first execution
        """
        # Store strong reference to handler to prevent GC
        handler_ref = handler
        token_ref: EventToken | None = None

        def wrapper(payload: Any) -> Any:
            """Wrapper that calls handler once then unsubscribes."""

            def _unsubscribe() -> None:
                if token_ref is not None:
                    self.off(event, token_ref)
                    self._once_handlers.pop(token_ref, None)

            try:
                result = handler_ref(payload)
            except Exception:
                _unsubscribe()
                raise

            if isinstance(result, AwaitableABC):
                # Unsubscribe immediately to prevent duplicate execution
                _unsubscribe()

                awaitable: AwaitableABC[Any] = result

                async def _await_once() -> Any:
                    return await awaitable

                return _await_once()

            _unsubscribe()
            return result

        # Subscribe the wrapper and store token
        token = self.subscribe(event, wrapper)
        token_ref = token

        # Store strong references to prevent garbage collection
        with self._lock:
            self._once_handlers[token] = (wrapper, handler_ref)

        return token

    async def emit_async(self, event: str, payload: Any = None) -> None:
        """Emit an event and await async handlers."""
        with self._lock:
            handlers = self._handlers.get(event, []).copy()

        if not handlers:
            logger.debug(f"No handlers for event '{event}'")
            return

        logger.debug(f"Async emitting '{event}' to {len(handlers)} handler(s)")

        dead_refs: list[HandlerRef] = []
        tasks: list[asyncio.Task[Any]] = []

        for handler_ref in handlers:
            handler = handler_ref()
            if handler is None:
                dead_refs.append(handler_ref)
                continue

            try:
                result = handler(payload)
            except Exception as exc:
                logger.error(
                    f"Handler error for '{event}': {exc}",
                    exc_info=True,
                )
                continue

            if isinstance(result, AwaitableABC):
                awaitable_result = result
                tasks.append(asyncio.create_task(self._await_handler(event, awaitable_result)))
            else:
                # Synchronous handler result is ignored
                continue

        if dead_refs:
            with self._lock:
                for ref in dead_refs:
                    with suppress(ValueError, KeyError):
                        self._handlers[event].remove(ref)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _await_handler(self, event: str, awaitable: AwaitableABC[Any]) -> None:
        """Await handler coroutine and log errors."""
        try:
            await awaitable
        except Exception as exc:
            logger.error(
                f"Async handler error for '{event}': {exc}",
                exc_info=True,
            )

    def _schedule_async_result(self, event: str, awaitable: AwaitableABC[Any]) -> None:
        """Schedule awaiting of async handler result on a running loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            loop.create_task(self._await_handler(event, awaitable))
            return

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(
                lambda: loop.create_task(self._await_handler(event, awaitable))
            )
            return

        logger.warning(
            "No running event loop to schedule async handler for '%s'; closing awaitable",
            event,
        )
        self._finalize_awaitable(awaitable)

    @staticmethod
    def _finalize_awaitable(awaitable: AwaitableABC[Any]) -> None:
        """Close or cancel awaitable to avoid un-awaited coroutine warnings."""
        if inspect.iscoroutine(awaitable):
            awaitable.close()
            return

        if isinstance(awaitable, asyncio.Future):
            awaitable.cancel()


# Global event bus instance
_global_bus_state: dict[str, EventBus | None] = {"bus": None}
_bus_lock = threading.Lock()


def _create_event_bus() -> EventBus:
    try:
        module = importlib.import_module("loomstroke.core.event_bus_extensions")
        cls = module.ExtendedEventBus
    except Exception:
        logger.info("Created global event bus")
        return EventBus()
    logger.info("Created global extended event bus")
    return cast(EventBus, cls())


def get_event_bus() -> EventBus:
    """Get the singleton event bus instance."""
    bus = _global_bus_state["bus"]
    if bus is None:
        with _bus_lock:
            bus = _global_bus_state["bus"]
            if bus is None:
                bus = _create_event_bus()
                _global_bus_state["bus"] = bus
    return bus


def reset_event_bus() -> None:
    """Reset the global event bus (mainly for testing)."""
    with _bus_lock:
        bus = _global_bus_state["bus"]
        if bus is not None:
            bus.clear()
        _global_bus_state["bus"] = None
        logger.info("Reset global event bus")
