"""Screen Mode visibility coordination for MainWindow."""

from __future__ import annotations

from typing import Any


class MainWindowScreenModeController:
    """Own Screen Mode visibility snapshot and restore operations."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def initialize_state(self) -> None:
        """Initialize Screen Mode state on the MainWindow compatibility surface."""
        self._window._screen_mode_active = False
        self._window._screen_mode_saved_docks = {}
        self._window._screen_mode_saved_toolbars = {}
        self._window._screen_mode_saved_statusbar = None
        self._window._screen_mode_action = None

    def toggle(self, on: bool | None = None) -> None:
        """Toggle Screen Mode and keep the menu action check state in sync."""
        active = bool(getattr(self._window, "_screen_mode_active", False))
        target = (not active) if on is None else bool(on)
        if target == active:
            return
        if target:
            self._window._enter_screen_mode()
        else:
            self._window._exit_screen_mode()
        action = getattr(self._window, "_screen_mode_action", None)
        if action is not None:
            action.blockSignals(True)
            try:
                action.setChecked(bool(getattr(self._window, "_screen_mode_active", False)))
            finally:
                action.blockSignals(False)

    def enter(self) -> None:
        """Record current visibility and hide dock/toolbar/statusbar."""
        self._window._screen_mode_saved_docks = {
            pid: dock.isVisible() for pid, dock in self._window._panel_docks.items()
        }
        self._window._screen_mode_saved_toolbars = {
            name: tb.isVisible() for name, tb in self._window._toolbars.items()
        }
        status = self._status_bar()
        self._window._screen_mode_saved_statusbar = (
            status.isVisible() if status is not None else None
        )

        self._freeze_layout_settings()
        try:
            for dock in self._window._panel_docks.values():
                dock.setVisible(False)
            for toolbar in self._window._toolbars.values():
                toolbar.setVisible(False)
            if status is not None:
                status.setVisible(False)
        finally:
            self._thaw_layout_settings()
        self._window._screen_mode_active = True

    def exit(self) -> None:
        """Restore the visibility captured by enter()."""
        self._freeze_layout_settings()
        try:
            for pid, visible in self._window._screen_mode_saved_docks.items():
                dock = self._window._panel_docks.get(pid)
                if dock is not None:
                    dock.setVisible(visible)
            for name, visible in self._window._screen_mode_saved_toolbars.items():
                toolbar = self._window._toolbars.get(name)
                if toolbar is not None:
                    toolbar.setVisible(visible)
            if self._window._screen_mode_saved_statusbar is not None:
                status = self._status_bar()
                if status is not None:
                    status.setVisible(self._window._screen_mode_saved_statusbar)
        finally:
            self._thaw_layout_settings()
        self._window._screen_mode_saved_docks = {}
        self._window._screen_mode_saved_toolbars = {}
        self._window._screen_mode_saved_statusbar = None
        self._window._screen_mode_active = False

    def _status_bar(self) -> Any | None:
        if not hasattr(self._window, "statusBar"):
            return None
        return self._window.statusBar()

    def _freeze_layout_settings(self) -> None:
        self._window._layout_settings_freeze = (
            getattr(self._window, "_layout_settings_freeze", 0) + 1
        )

    def _thaw_layout_settings(self) -> None:
        self._window._layout_settings_freeze = max(
            0,
            getattr(self._window, "_layout_settings_freeze", 1) - 1,
        )
