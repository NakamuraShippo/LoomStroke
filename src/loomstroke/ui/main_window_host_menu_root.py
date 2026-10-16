"""Host binding and menu root setup for :class:`MainWindow`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtWidgets import QMenuBar, QWidget

logger = logging.getLogger(__name__)

MenuBarFactory = Callable[[Any], QMenuBar]


class MainWindowHostMenuRootCoordinator:
    """Initialize MainWindow host references and menu root widget."""

    def __init__(
        self,
        window: Any,
        *,
        menu_bar_factory: MenuBarFactory = QMenuBar,
    ) -> None:
        self._window = window
        self._menu_bar_factory = menu_bar_factory

    def initialize(self, host: Any, registry: Any) -> None:
        """Bind host references and normalize the QMainWindow menu root."""
        window = self._window
        window.host = host
        window.registry = registry
        window._plugin_registry = registry
        window._host_api = host
        self._ensure_menu_root()

    def _ensure_menu_root(self) -> None:
        window = self._window
        try:
            if window.menuWidget() is not None:
                window.setMenuWidget(cast(QWidget, None))
        except Exception:
            logger.debug("_initialize_host_and_menu_root: suppressed exception", exc_info=True)

        menu_bar = window.menuBar()
        if menu_bar is None:
            menu_bar = self._menu_bar_factory(window)
            window.setMenuBar(menu_bar)

        menu_bar.setVisible(True)
        menu_bar.setNativeMenuBar(False)
