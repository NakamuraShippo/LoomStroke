"""Check one prompt's presence in the queue of its submitting server."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def queue_reader(connection: Any) -> Any:
    return getattr(getattr(connection, "async_manager", None), "get_json", None)


def _queue_contains_prompt(snapshot: Any, prompt_id: str) -> bool | None:
    if not isinstance(snapshot, dict):
        return None
    present = False
    for name in ("queue_running", "queue_pending"):
        entries = snapshot.get(name)
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                return None
            identifier = entry[1]
            if not isinstance(identifier, str) or not identifier:
                return None
            if identifier == prompt_id:
                present = True
    return present


class QueueAbsence:
    def __init__(self, *, interval: float, grace: float) -> None:
        self._interval = interval
        self._grace = grace
        self._next_read = 0.0
        self._absent_since: float | None = None

    async def confirmed(self, connection: Any, prompt_id: str) -> bool:
        loop = asyncio.get_running_loop()
        read = queue_reader(connection)
        if not callable(read) or loop.time() < self._next_read:
            return False
        try:
            snapshot = await read(f"{connection.base_url}/queue")
        except Exception as exc:  # noqa: BLE001 - unsupported or unavailable queue is unknown
            logger.debug("ComfyUI queue observation unavailable (%s)", type(exc).__name__)
            snapshot = None
        now = loop.time()
        self._next_read = now + self._interval
        if _queue_contains_prompt(snapshot, prompt_id) is not False:
            self._absent_since = None
            return False
        if self._absent_since is None:
            self._absent_since = now
            return False
        return now - self._absent_since >= self._grace
