"""Visible selection-tip failures for optionally marked log-only UI callables."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from loomstroke.plugins.common.ui_notification_hint import has_log_only_ui_hint

from .pen_notice import _gui_application, _PenNotice

logger = logging.getLogger(__name__)

_TITLE_SOURCE = "Brush Manager"
_FAILURE_SOURCE = "The brush tip could not be created."


def _uses_standard_log_only_ui(ui: Any) -> bool:
    return has_log_only_ui_hint(ui)


def clear_selection_tip_feedback(plugin: Any) -> None:
    feedback = getattr(plugin, "_selection_tip_feedback", None)
    if feedback is None:
        return
    try:
        if _gui_application() is None:
            return
        feedback.close()
        plugin._selection_tip_feedback = None
    except Exception:
        logger.warning("Basic Pen tip notice could not be closed")


def show_selection_tip_failure(plugin: Any, translate: Callable[[str], str]) -> None:
    """Preserve custom-host routing and never retry a standard notice as modal."""
    host = plugin.host_api
    if not _uses_standard_log_only_ui(getattr(host, "ui", None)):
        plugin._notify(translate(_FAILURE_SOURCE), level="error")
        return
    try:
        if _gui_application() is None:
            logger.warning("Basic Pen tip feedback requires the GUI thread")
            return
        feedback = getattr(plugin, "_selection_tip_feedback", None)
        if feedback is None:
            feedback = _PenNotice(getattr(plugin, "_ts", translate), _TITLE_SOURCE, _FAILURE_SOURCE)
            plugin._selection_tip_feedback = feedback
        feedback.show(host)
    except Exception:
        clear_selection_tip_feedback(plugin)
        logger.warning("Basic Pen tip failure could not be displayed")
