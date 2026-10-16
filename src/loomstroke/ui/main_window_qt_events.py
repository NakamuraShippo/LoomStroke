"""Qt event shell coordination for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QChildEvent, QEvent
from PySide6.QtWidgets import QDockWidget

from ._dock_safe import is_valid

logger = logging.getLogger(__name__)

SingleShot = Callable[[int, Callable[[], None]], None]


class MainWindowQtEventShellCoordinator:
    """Dispatch Qt override side effects without owning the override entrypoints."""

    def __init__(self, window: Any, *, single_shot: SingleShot) -> None:
        self._window = window
        self._single_shot = single_shot

    def handle_change_event(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self._window._refresh_translations()

    def handle_move_or_resize_event(self) -> None:
        self._window._sync_floating_chrome_positions()

    def handle_child_event(self, event: QChildEvent) -> None:
        if event.type() != QEvent.Type.ChildAdded:
            return
        child = event.child()
        if isinstance(child, QDockWidget):
            self._window._ensure_dock_tracking(child)

    def dock_filter_target(self, obj: object) -> QDockWidget | None:
        if not isinstance(obj, QDockWidget):
            return None
        if not is_valid(obj) or self._window._is_spacer_dock(obj):
            return None
        return obj

    def handle_dock_filter_event(self, dock: QDockWidget, event: QEvent) -> None:
        self._window._handle_dock_filter_event(dock, event)

    def handle_show_event(self) -> None:
        window = self._window
        if not getattr(window, "_dock_collapse_ready", False):
            self._single_shot(500, window._enable_dock_collapse)

        menu_bar = window.menuBar()
        if menu_bar is not None and not menu_bar.isVisible():
            logger.warning("MenuBar hidden on show! Forcing visibility...")
            menu_bar.setVisible(True)

        window._sync_floating_chrome_positions()
        window._debug_menubar_state()
