"""MainWindow Undo/Redo action state and dispatch coordination."""

from __future__ import annotations

import logging
from typing import Any

from loomstroke.ui.history_action_presentation import HistoryActionPresenter
from loomstroke.ui.inline_text_history_target import inline_text_history_target

logger = logging.getLogger("loomstroke.ui.main_window")


class MainWindowHistoryActionCoordinator:
    """Own MainWindow-side history action enablement and dispatch policy."""

    def __init__(self, window: Any) -> None:
        self._window = window
        self._presenter = HistoryActionPresenter(window)

    def initialize_state(self) -> None:
        """Initialize Undo/Redo action state on the MainWindow compatibility surface."""
        self._window._undo_action = None
        self._window._redo_action = None
        self._window._history_can_undo = None
        self._window._history_can_redo = None
        self._window._tool_can_undo = False

    def update_history_actions(
        self,
        *,
        can_undo: bool | None = None,
        can_redo: bool | None = None,
    ) -> None:
        window = self._window
        undo_action = window._undo_action
        redo_action = window._redo_action
        if undo_action is None and redo_action is None:
            return
        if can_undo is not None:
            window._history_can_undo = bool(can_undo)
        if can_redo is not None:
            window._history_can_redo = bool(can_redo)

        history_can_undo = bool(window._history_can_undo)
        history_can_redo = bool(window._history_can_redo)
        tool_can_undo = bool(window._tool_can_undo)
        editor = inline_text_history_target(window)
        if editor is not None:
            if undo_action is not None:
                undo_action.setEnabled(editor.document().isUndoAvailable())
            if redo_action is not None:
                redo_action.setEnabled(editor.document().isRedoAvailable())
            self._presenter.refresh_labels()
            return

        if undo_action is not None:
            undo_action.setEnabled(history_can_undo or tool_can_undo)
        if redo_action is not None:
            redo_action.setEnabled(history_can_redo)
        # CT-05 H5c: re-derive the candidate-aware labels/tips from the
        # current unified state — runs on every history change, after
        # menu rebuilds (build_menubar calls back in here), and after
        # runtime language switches.  No-ops on windows without the
        # shortcut-label surface.
        self._presenter.refresh_labels()

    def on_undo(self) -> None:
        window = self._window
        editor = inline_text_history_target(window)
        if editor is not None:
            editor.undo()
            self.update_history_actions()
            return
        if hasattr(window, "_host_api") and window._host_api:
            tool_manager = getattr(window._host_api, "_tool_manager", None)
            if tool_manager is not None and hasattr(tool_manager, "dispatch_undo"):
                try:
                    if tool_manager.dispatch_undo():
                        return
                except Exception:
                    logger.debug("Tool undo dispatch failed", exc_info=True)
            window._host_api.history.undo()
            return
        window._event_bus.emit("history.undo", {})

    def on_redo(self) -> None:
        window = self._window
        editor = inline_text_history_target(window)
        if editor is not None:
            editor.redo()
            self.update_history_actions()
            return
        if hasattr(window, "_host_api") and window._host_api:
            window._host_api.history.redo()
        else:
            window._event_bus.emit("history.redo", {})
