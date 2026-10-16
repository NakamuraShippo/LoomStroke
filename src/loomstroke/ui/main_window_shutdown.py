"""Shutdown and close-event coordination for MainWindow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class MainWindowShutdownCoordinator:
    """Coordinate MainWindow close-event prompt and shutdown cleanup."""

    def __init__(
        self,
        window: Any,
        *,
        message_box: Callable[[], Any],
        application_instance: Callable[[], Any],
        dock_widget_type: Callable[[], Any],
        is_valid_widget: Callable[[Any], bool],
        log_close: Callable[[], None],
    ) -> None:
        self._window = window
        self._message_box = message_box
        self._application_instance = application_instance
        self._dock_widget_type = dock_widget_type
        self._is_valid_widget = is_valid_widget
        self._log_close = log_close

    def close_event(
        self,
        event: Any,
        *,
        base_close_event: Callable[[Any], None],
    ) -> None:
        """Handle MainWindow closeEvent while preserving existing ordering."""
        if self._handle_unsaved_changes(event):
            return
        self._run_shutdown_cleanup(event, base_close_event=base_close_event)

    def _handle_unsaved_changes(self, event: Any) -> bool:
        window = self._window
        if getattr(window, "_closing_in_progress", False) or not getattr(
            window, "_document_dirty", False
        ):
            return False

        message_box = self._message_box()
        buttons = (
            message_box.StandardButton.Save
            | message_box.StandardButton.Discard
            | message_box.StandardButton.Cancel
        )
        reply = message_box.question(
            window,
            window._ts("Unsaved Changes"),
            window._ts("You have unsaved changes. Do you want to save before closing?"),
            buttons,
            message_box.StandardButton.Save,
        )
        if reply == message_box.StandardButton.Cancel:
            event.ignore()
            return True
        if reply != message_box.StandardButton.Save:
            return False

        target = window._document_path or window._prompt_save_path()
        if target is None:
            event.ignore()
            return True
        if window._try_start_close_save_async(target):
            event.ignore()
            return True
        window._perform_save(target)
        return False

    def _run_shutdown_cleanup(
        self,
        event: Any,
        *,
        base_close_event: Callable[[Any], None],
    ) -> None:
        window = self._window
        window.window_closing.emit()
        schedule_mcp_shutdown = getattr(window, "_schedule_mcp_runtime_shutdown", None)
        if callable(schedule_mcp_shutdown):
            schedule_mcp_shutdown()
        window._save_window_state()
        window._disconnect_event_bus_subscriptions()

        timer = getattr(window, "_collapse_timer", None)
        if timer is not None:
            timer.stop()

        for dock in list(window.findChildren(self._dock_widget_type())):
            if self._is_valid_widget(dock):
                dock.hide()

        self._log_close()
        base_close_event(event)

        app = self._application_instance()
        if app is not None:
            app.quit()
