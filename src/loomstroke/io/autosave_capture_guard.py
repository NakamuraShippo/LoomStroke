"""Optional capture-readiness hints for built-in and custom document services."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def capture_is_blocked(service: Any) -> bool:
    """Only an explicit built-in boolean blocks the periodic tick."""
    return _query_true(service, "_document_capture_blocked")


def consume_deferred_autosave(service: Any, path: Path) -> bool:
    """Consume only the deferred result belonging to this completed attempt."""
    return _query_true(service, "_consume_deferred_autosave", path)


def _query_true(service: Any, name: str, *args: Any) -> bool:
    try:
        query = getattr(service, name, None)
        return callable(query) and query(*args) is True
    except Exception:
        # A custom service need not implement these internal capabilities.
        # Its normal save result remains authoritative when a hint fails.
        logger.debug("Optional autosave capture query failed: %s", name)
        return False
