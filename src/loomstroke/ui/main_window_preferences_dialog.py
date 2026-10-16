"""MainWindow preferences dialog opening coordination."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class MainWindowPreferencesDialogCoordinator:
    """Own MainWindow-side Preferences dialog open policy."""

    def __init__(self, window: Any, *, dialog_type: Callable[[], type[Any]]) -> None:
        self._window = window
        self._dialog_type = dialog_type

    def set_last_hovered_shortcut(self, action_id: str) -> None:
        self._window._last_hovered_shortcut = action_id

    def open_hover_shortcut_preferences(self) -> None:
        self.open_shortcut_preferences(getattr(self._window, "_last_hovered_shortcut", None))

    def on_preferences(self) -> None:
        dialog = self._create_dialog()
        dialog.exec()

    def open_shortcut_preferences(self, action_id: str | None = None) -> None:
        dialog = self._create_dialog()
        dialog.show_shortcuts_page(action_id)
        dialog.exec()

    def _create_dialog(self) -> Any:
        window = self._window
        dialog_type = self._dialog_type()
        endpoint_provider = getattr(window, "mcp_runtime_endpoint_info", None)
        token_refresher = getattr(window, "mcp_runtime_refresh_session_token", None)
        return dialog_type(
            window._settings,
            window._apply_preference_changes,
            window._shortcut_manager,
            parent=window,
            mcp_endpoint_info_provider=endpoint_provider if callable(endpoint_provider) else None,
            mcp_session_token_refresh_callback=(
                token_refresher if callable(token_refresher) else None
            ),
        )
