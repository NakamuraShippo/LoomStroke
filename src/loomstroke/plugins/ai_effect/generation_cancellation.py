"""Finish one acquired job's stop request before releasing its generation slot."""

from __future__ import annotations

import asyncio
import logging

from .gateway_protocol import JobHandle

logger = logging.getLogger(__name__)


async def _cancel_acquired_job(handle: JobHandle) -> None:
    """Await best-effort cancellation, including after repeated caller cancels.

    The caller still owns its semaphore/lock and re-raises its original
    CancelledError afterwards. A secondary stop failure must not replace it.
    """
    stop = asyncio.create_task(handle.cancel())
    while not stop.done():
        try:
            await asyncio.shield(stop)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    try:
        stop.result()
    except (Exception, asyncio.CancelledError):
        # Backend exception text may contain request or credential details.
        logger.warning("AI Effect job stop request did not complete successfully")
