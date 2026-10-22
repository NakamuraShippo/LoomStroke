"""Top-level window shell bootstrap for MainWindow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMainWindow

from .. import __version__
from .theme import image_asset_path


class MainWindowWindowSetupCoordinator:
    """Coordinate MainWindow title, icon, size, and dock option bootstrap."""

    def __init__(
        self,
        window: Any,
        *,
        asset_path: Callable[[str], Path] = image_asset_path,
        icon_type: type[QIcon] = QIcon,
    ) -> None:
        self._window = window
        self._asset_path = asset_path
        self._icon_type = icon_type

    def setup_window(self) -> None:
        window = self._window
        base_title = window._ts("LoomStroke v{version}")
        window._base_window_title = base_title.format(version=__version__)
        window._translation_binder.register(
            lambda text: window._apply_window_title_translation(text.format(version=__version__)),
            base_title,
        )
        window._update_window_title()
        window.resize(1280, 800)
        self._apply_window_icon()
        window.setDockOptions(self._dock_options())

    def _apply_window_icon(self) -> None:
        icon_path = self._asset_path("favicon.png")
        if icon_path.exists():
            self._window.setWindowIcon(self._icon_type(str(icon_path)))

    @staticmethod
    def _dock_options() -> QMainWindow.DockOption:
        return QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks
