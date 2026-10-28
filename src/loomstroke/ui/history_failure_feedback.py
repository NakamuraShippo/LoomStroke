"""Present failed document-history requests without changing their routing."""

from __future__ import annotations

import logging
from inspect import getattr_static
from typing import Any

logger = logging.getLogger(__name__)

_FAILURE_SOURCE = "Undo/Redo could not be completed. Check the document before continuing."


def _declared_method(history: Any, name: str) -> Any:
    # Probing optional metadata must not fabricate attributes on dynamic hosts.
    if getattr_static(history, name, None) is None:
        return None
    return getattr(history, name, None)


def _has_candidate(history: Any, direction: str) -> bool:
    # Blocked routed history still has a candidate while can_undo/can_redo is
    # False. Canvas-only services expose availability without routed metadata.
    try:
        peek = _declared_method(history, f"_peek_{direction}_candidate")
        if callable(peek) and peek() is not None:
            return True
    except Exception:
        logger.debug("History candidate metadata is unavailable")
    try:
        available = _declared_method(history, f"can_{direction}")
        if callable(available):
            return bool(available())
    except Exception:
        logger.debug("History availability is unavailable")
    return False


def request_history(window: Any, history: Any, direction: str) -> Any:
    """Dispatch once and explain a False result only for an existing candidate."""
    had_candidate = _has_candidate(history, direction)
    result = getattr(history, direction)()
    if result is False and had_candidate:
        # Core dirty/state notifications run during dispatch. Explain the failure
        # afterwards so a document-modified status cannot immediately replace it.
        try:
            window.statusBar().showMessage(window._ts(_FAILURE_SOURCE), 5000)
        except Exception:
            logger.debug("History failure feedback could not be displayed")
    return result
