"""Helpers for interacting with the extended event bus."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def request_with_timeout(
    event_bus: Any,
    event: Any,
    *,
    timeout: float,
) -> Any:
    """Invoke `event_bus.request` with graceful fallback.

    Some test doubles replace ``request`` with simple callables that do not
    accept keyword arguments.  In that scenario we retry without the timeout
    keyword to keep backward compatibility.
    """

    request = getattr(event_bus, "request", None)
    if not callable(request):
        raise AttributeError("event bus does not expose a request() coroutine")

    try:
        return await request(event, timeout=timeout)
    except TypeError as exc:
        message = str(exc)
        if "unexpected keyword argument" not in message and "positional" not in message:
            raise
        logger.debug("Retrying event bus request without timeout keyword: %s", message)
        return await request(event)


__all__ = ["request_with_timeout"]
