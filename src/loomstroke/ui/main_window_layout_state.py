"""Coordinate MainWindow layout state load/save side effects."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import QByteArray

from ..host_api import UILayoutService, UILayoutSnapshot

logger = logging.getLogger(__name__)


class MainWindowLayoutStateCoordinator:
    """Coordinate startup layout restore and shutdown layout persistence."""

    def __init__(
        self,
        window: Any,
        *,
        single_shot: Callable[[int, Callable[[], None]], None],
        load_initial_layout: Callable[[], UILayoutSnapshot | None],
    ) -> None:
        self._window = window
        self._single_shot = single_shot
        self._load_initial_layout = load_initial_layout

    def initialize_state(self) -> None:
        """Initialize layout restore and feedback-guard state."""
        self._window._layout_settings_freeze = 0
        self._window._layout_restore_attempts = 0
        self._window._layout_restored = False

    def freeze_layout_settings(self, freeze: bool) -> None:
        """Temporarily suspend automatic layout persistence."""
        if freeze:
            self._window._layout_settings_freeze += 1
        else:
            self._window._layout_settings_freeze = max(
                0,
                self._window._layout_settings_freeze - 1,
            )

    def load_window_state(self) -> None:
        """Load legacy window geometry/state when the current policy allows it."""

        window = self._window
        settings = window._settings
        if not settings.get_bool("ui/restore_window_state", False):
            logger.info("Window state restore disabled; using defaults")
            return

        geometry = settings.get("ui/window_geometry")
        if geometry and isinstance(geometry, str):
            try:
                window.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
            except Exception:
                logger.warning("Failed to restore window geometry", exc_info=True)

        layout_persistence = getattr(window, "_layout_persistence", None)
        if layout_persistence is not None:
            try:
                if not layout_persistence.layout_exists():
                    logger.info("Skipping legacy window state; bundled layout will restore docks")
                    return
            except Exception:
                logger.warning(
                    "Failed to inspect saved layout before legacy window state restore; "
                    "skipping legacy state",
                    exc_info=True,
                )
                return

        state = settings.get("ui/window_state")
        if state and isinstance(state, str):
            try:
                window.restoreState(QByteArray.fromBase64(state.encode()))
            except Exception:
                logger.warning("Failed to restore window state", exc_info=True)

    def save_window_state(self) -> None:
        """Save window state while preserving the visible layout transaction."""

        window = self._window
        if getattr(window, "_layout_settings_freeze", 0) > 0:
            return

        clear_transient_dock_spacers = getattr(window, "clear_transient_dock_spacers", None)
        if callable(clear_transient_dock_spacers):
            clear_transient_dock_spacers()

        screen_mode_was_active = getattr(window, "_screen_mode_active", False)
        if screen_mode_was_active:
            window._exit_screen_mode()

        geometry = bytes(window.saveGeometry().toBase64().data()).decode()
        window._settings.set("ui/window_geometry", geometry)

        state = bytes(window.saveState().toBase64().data()).decode()
        window._settings.set("ui/window_state", state)

        window._settings.set("ui/restore_window_state", True)
        window._settings.set("ui/recent_files", window._recent_files)

        window._save_dock_sizes()
        save_floating_tool_toolbar_state = getattr(
            window,
            "_save_floating_tool_toolbar_state",
            None,
        )
        if callable(save_floating_tool_toolbar_state):
            save_floating_tool_toolbar_state()

        if getattr(window, "_layout_persistence", None) is not None:
            window._layout_persistence.save_current_layout()
        if getattr(window, "_workflow_layouts", None) is not None:
            window._save_active_comfyui_layout()

        if screen_mode_was_active:
            window._enter_screen_mode()

    def schedule_layout_restore(self) -> None:
        """Attempt to restore the persisted layout once the service is ready."""

        window = self._window
        if getattr(window, "_layout_persistence", None) is None:
            return
        self._single_shot(0, window._attempt_layout_restore)

    def attempt_layout_restore(self) -> None:
        window = self._window
        if getattr(window, "_layout_persistence", None) is None:
            return
        if getattr(window, "_layout_restored", False):
            return
        service = window._layout_service_getter()
        if service is None:
            if window._layout_restore_attempts < 10:
                window._layout_restore_attempts += 1
                self._single_shot(250, window._attempt_layout_restore)
            return
        if window._layout_persistence.layout_exists():
            snapshot = window._layout_persistence.load_layout()
            if snapshot is not None:
                window._apply_layout_snapshot(snapshot, clear_missing=False, service=service)
            window._layout_restored = True
            return

        snapshot = self._load_initial_layout()
        if snapshot is not None:
            logger.info("Applying bundled first-run layout")
            window._apply_layout_snapshot(
                snapshot,
                clear_missing=False,
                service=service,
                allow_restore_state=bool(snapshot.window_state),
                force_panel_layout=True,
            )
        window._layout_restored = True

    def apply_layout_snapshot(
        self,
        snapshot: UILayoutSnapshot,
        *,
        clear_missing: bool,
        allow_restore_state: bool = True,
        force_panel_layout: bool = False,
        service: UILayoutService | None = None,
    ) -> None:
        window = self._window
        layout_service = service or window._layout_service_getter()
        if layout_service is None:
            return
        window.freeze_layout_settings(True)
        try:
            if force_panel_layout:
                cast(Any, layout_service).apply(
                    snapshot,
                    allow_restore_state=allow_restore_state,
                    clear_missing=clear_missing,
                    force_panel_layout=True,
                )
            else:
                layout_service.apply(
                    snapshot,
                    allow_restore_state=allow_restore_state,
                    clear_missing=clear_missing,
                )
        except Exception as exc:
            logger.warning("Failed to apply layout snapshot: %s", exc)
        finally:
            window.freeze_layout_settings(False)

    def layout_service_getter(self) -> UILayoutService | None:
        window = self._window
        host = getattr(window, "_host_api", None) or getattr(window, "host", None)
        if host is None:
            return None
        ui_service = getattr(host, "ui", None)
        getter = getattr(ui_service, "get_layout_service", None)
        if not callable(getter):
            return None
        try:
            return cast(UILayoutService | None, getter())
        except Exception as exc:
            logger.debug("Layout service getter raised: %s", exc)
            return None
