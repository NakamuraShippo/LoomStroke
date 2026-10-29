"""Bounded, serial generation scheduling, independent of Qt and gateways."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def run_batch(
    *,
    limit: int | None,
    generate: Callable[[], Awaitable[str | None]],
    is_current: Callable[[], bool],
    progress: Callable[[int, int | None], None],
) -> tuple[int, str]:
    """Await each complete generation; never enqueue the remainder in advance.

    Cancellation propagates into the existing generation controller, which
    owns backend cancellation and post-success protection. Cache hits retain
    the ordinary single-generation behavior and count as completed attempts.
    """
    if limit is not None and limit < 1:
        raise ValueError("Generation count must be positive")
    completed = 0
    while limit is None or completed < limit:
        if not is_current():
            return completed, "stopped"
        status = await generate()
        if status not in {"success", "cache_hit"}:
            return completed, "failed"
        completed += 1
        progress(completed, limit)
        # Give Qt input/stop and page-switch events time even on instant cache hits.
        await asyncio.sleep(0.05)
    return completed, "finished"
