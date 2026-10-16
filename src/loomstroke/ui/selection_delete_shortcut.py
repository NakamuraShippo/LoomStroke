"""Route the Delete shortcut to selected pixels without changing Layer > Delete."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction, QKeySequence, QShortcutEvent

from loomstroke.core.commands import CutSelectionCommand

logger = logging.getLogger(__name__)


class SelectionDeleteShortcutFilter(QObject):
    """An action-owned filter; menu clicks retain explicit layer deletion."""

    def __init__(self, action: QAction, window: Any) -> None:
        super().__init__(action)
        self._window = window
        action.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() != QEvent.Type.Shortcut
            or not isinstance(event, QShortcutEvent)
            or event.key() != QKeySequence("Del")
            or event.isAmbiguous()
        ):
            return False
        canvas = getattr(getattr(self._window, "_host_api", None), "canvas", None)
        getter = getattr(canvas, "get_selection", None)
        if not callable(getter) or getter() is None:
            return False
        # A failed/locked selection edit must never become a whole-layer delete.
        # Direct canvas submission reuses mask/undo/texture handling, without the
        # ToolManager cut hook that publishes a payload to the clipboard.
        try:
            canvas.begin_transaction("Cut Selection")
            try:
                canvas.submit(CutSelectionCommand(None))
            finally:
                canvas.end_transaction()
        except Exception:
            logger.exception("Delete selection failed; layer deletion suppressed")
        return True


def install_selection_delete_shortcut(action: QAction, window: Any) -> None:
    action._selection_delete_filter = SelectionDeleteShortcutFilter(action, window)
