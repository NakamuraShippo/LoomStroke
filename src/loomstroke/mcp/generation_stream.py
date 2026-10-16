"""Check privacy before each stream advance and close credential-owning iterators."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


async def guarded_stream(service: Any, stream: AsyncIterator[Any]) -> AsyncIterator[Any]:
    iterator = stream.__aiter__()
    try:
        while True:
            service.safety.require_external_services()
            try:
                event = await anext(iterator)
            except StopAsyncIteration:
                return
            yield event
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()
