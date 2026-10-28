"""Wake one service-backed handle without relying on a backend cancel event."""

from __future__ import annotations

import asyncio
from typing import Any

from loomstroke.plugins.ai_effect.gateway_protocol import ProgressEvent


def _cancel_handle_progress(handle: Any) -> bool:
    """Publish local cancellation once, then request its history poll to stop."""
    if handle._cancelled:
        return False
    handle._cancelled = True
    handle._progress_queue.put_nowait(ProgressEvent(kind="cancelled"))
    poll = handle._poll_task
    # A cancelled history may be accepted by this very poll. Let its ordinary
    # return finish the task instead of scheduling cancellation of itself.
    if (
        poll is not None
        and not poll.done()
        and poll is not asyncio.current_task(loop=poll.get_loop())
    ):
        poll.cancel()
    return True
