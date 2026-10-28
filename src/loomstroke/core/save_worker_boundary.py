"""Keep a cancelled save's writer inside the owning save/residency boundary."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

_Result = TypeVar("_Result")


async def complete_save_worker(
    commit: Callable[..., _Result], *args: Any, logger: logging.Logger
) -> _Result:
    """Propagate cancellation only after this worker can no longer replace files.

    Cancelling ``to_thread`` stops waiting, not its running thread. Shield the
    private worker and drain it even if cancellation is requested repeatedly.
    The caller keeps its save guard and page lease until this function exits.
    """
    worker = asyncio.create_task(asyncio.to_thread(commit, *args))
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError as exc:
            if worker.cancelled():
                # A private worker is never cancelled by its save caller.
                # This can only happen during loop-wide shutdown.
                raise
            cancellation = exc
        except Exception:
            if cancellation is not None:
                logger.exception("Document save worker failed while waiting for cancellation")
                raise cancellation from None
            raise
        else:
            if cancellation is not None:
                raise cancellation
            return result
