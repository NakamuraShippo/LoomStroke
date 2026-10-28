"""A plugin-owned, nonmodal notice for brush-store preparation failures only."""

from __future__ import annotations

import logging
from typing import Any

from .pen_notice import _gui_application, _PenNotice

logger = logging.getLogger(__name__)

_TITLE_SOURCE = "Basic Pen"
_FAILURE_SOURCE = "Basic Pen could not be activated because its brush data could not be prepared."


def clear_startup_feedback(plugin: Any) -> None:
    """Close only this plugin's notice, without touching an unavailable GUI."""
    feedback = getattr(plugin, "_startup_feedback", None)
    if feedback is None:
        return
    try:
        if _gui_application() is None:
            return
        feedback.close()
        plugin._startup_feedback = None
    except Exception:
        logger.warning("Basic Pen startup notice could not be closed")


def show_startup_failure(plugin: Any, host: Any) -> None:
    """Do not obscure the preparation error with a secondary UI failure."""
    try:
        if _gui_application() is None:
            logger.warning("Basic Pen startup feedback requires the GUI thread")
            return
        feedback = getattr(plugin, "_startup_feedback", None)
        if feedback is None:
            feedback = _PenNotice(plugin._ts, _TITLE_SOURCE, _FAILURE_SOURCE)
            plugin._startup_feedback = feedback
        feedback.show(host)
    except Exception:
        clear_startup_feedback(plugin)
        # Keep exception text, paths and stored data out of new UI/log messages.
        logger.warning("Basic Pen startup failure could not be displayed")
