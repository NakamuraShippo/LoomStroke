"""Event Bus extensions for request-response pattern.

This module extends the base EventBus with request-response capabilities
needed for plugin communication.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from loomstroke.core.event_bus import EventBus, EventToken
from loomstroke.plugins.common.events import PluginEvent

logger = logging.getLogger(__name__)


class EventBusTimeoutError(Exception):
    """Raised when a request times out waiting for response."""

    pass


class ExtendedEventBus(EventBus):
    """EventBus with request-response pattern support.

    This extends the base EventBus to support synchronous-like
    request-response communication between plugins.
    """

    def __init__(self) -> None:
        """Initialize the extended event bus."""
        super().__init__()
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._response_handlers: dict[str, list[tuple[EventToken, Any]]] = {}

    async def request(self, event: PluginEvent, timeout: float = 5.0) -> Any:
        """Send a request and wait for response.

        This method implements the request-response pattern for
        synchronous-like communication between plugins.

        Args:
            event: The event to send (should have correlation_id)
            timeout: Maximum time to wait for response (seconds)

        Returns:
            The response data from the handler

        Raises:
            EventBusTimeoutError: If no response within timeout
        """
        # Ensure event has correlation_id
        if not event.correlation_id:
            event = PluginEvent.create_request(
                event_type=event.event_type,
                source_plugin=event.source_plugin,
                payload=event.payload,
            )

        correlation_id = event.correlation_id

        # Create future for response
        future = asyncio.Future()
        self._pending_requests[correlation_id] = future

        # Register temporary response handler
        def response_handler(response_event: PluginEvent) -> None:
            """Handle response for this specific request."""
            if (
                response_event.correlation_id == correlation_id
                and correlation_id in self._pending_requests
            ):
                self._pending_requests[correlation_id].set_result(response_event.payload)

        # Subscribe to response event
        response_event_type = f"{event.event_type}.response"
        response_token = self.on(response_event_type, response_handler)

        try:
            # Emit the request
            await self.emit_async(event.event_type, event)

            # Wait for response with timeout
            result = await asyncio.wait_for(future, timeout=timeout)
            return result

        except TimeoutError:
            logger.warning(
                f"Request timeout for {event.event_type} (correlation_id: {correlation_id})"
            )
            raise EventBusTimeoutError(  # noqa: B904
                f"No response received for {event.event_type} within {timeout}s"
            )

        finally:
            # Clean up
            self._pending_requests.pop(correlation_id, None)
            self.off(response_event_type, response_token)

    async def respond(self, correlation_id: str, event_type: str, response_data: Any) -> None:
        """Send a response to a request.

        This method is used by event handlers to send responses
        back to requesters.

        Args:
            correlation_id: The correlation ID from the original request
            event_type: The original event type (response will be sent to {event_type}.response)
            response_data: The response data to send
        """
        response_event = PluginEvent(
            event_type=f"{event_type}.response",
            source_plugin="system",  # Can be overridden
            payload=response_data,
            correlation_id=correlation_id,
        )

        await self.emit_async(response_event.event_type, response_event)

    def register_request_handler(self, event_type: str, handler: Any) -> EventToken:
        """Register a handler that can respond to requests.

        This is a convenience method that wraps a handler to automatically
        handle the request-response pattern.

        Args:
            event_type: The event type to handle
            handler: Async function that processes request and returns response

        Returns:
            Token that can be used to unregister the handler
        """

        async def wrapped_handler(event: PluginEvent) -> None:
            """Wrapper that handles request and sends response."""
            if not isinstance(event, PluginEvent):
                logger.warning(f"Invalid event type for {event_type}: {type(event)}")
                return

            if not event.correlation_id:
                # This is a notification, not a request
                await handler(event)
                return

            try:
                # Process request
                result = await handler(event)

                # Send response
                await self.respond(
                    correlation_id=event.correlation_id,
                    event_type=event.event_type,
                    response_data=result,
                )

            except Exception as e:
                logger.error(f"Error handling request {event_type}: {e}", exc_info=True)
                # Send error response
                error_response = {"success": False, "error": str(e)}
                await self.respond(
                    correlation_id=event.correlation_id,
                    event_type=event.event_type,
                    response_data=error_response,
                )

        # Register the wrapped handler and keep a strong reference
        token = self.on(event_type, wrapped_handler)
        registry = self._response_handlers.setdefault(event_type, [])
        registry.append((token, wrapped_handler))
        return token

    def unregister_request_handler(self, event_type: str, token: EventToken) -> None:
        """Unregister a previously registered request handler."""
        handlers = self._response_handlers.get(event_type)
        if handlers:
            self._response_handlers[event_type] = [entry for entry in handlers if entry[0] != token]
            if not self._response_handlers[event_type]:
                self._response_handlers.pop(event_type, None)
        self.off(event_type, token)


# Singleton delegation — canonical instance lives in event_bus._global_bus_state


def get_extended_event_bus() -> ExtendedEventBus:
    """Get the global extended event bus instance.

    Delegates to ``get_event_bus()`` so that a single instance is shared.

    Returns:
        Global ExtendedEventBus instance
    """
    from loomstroke.core.event_bus import get_event_bus  # noqa: PLC0415

    bus = get_event_bus()
    assert isinstance(bus, ExtendedEventBus)
    return bus


def reset_extended_event_bus() -> None:
    """Reset the global extended event bus (mainly for testing).

    Delegates to ``reset_event_bus()``.
    """
    from loomstroke.core.event_bus import reset_event_bus  # noqa: PLC0415

    reset_event_bus()
