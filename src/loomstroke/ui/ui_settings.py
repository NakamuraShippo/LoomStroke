"""UI settings management for window and panel states."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QDockWidget, QMainWindow, QToolBar

from loomstroke.platform.settings_categories import UISettings
from loomstroke.platform.settings_persistence import SettingsPersistence

logger = logging.getLogger(__name__)


class UISettingsManager:
    """Manages UI settings including window geometry and panel states.

    Provides save/restore functionality for window layout, panel visibility,
    toolbar positions, and other UI-related settings.
    """

    def __init__(self, persistence: SettingsPersistence | None = None):
        """Initialize UI settings manager.

        Args:
            persistence: Settings persistence instance
        """
        self.persistence = persistence or SettingsPersistence()
        self.ui_settings = UISettings()

        # Load UI settings
        self.load_settings()

        # Cache for panel states
        self._panel_states: dict[str, dict[str, Any]] = {}
        self._toolbar_states: dict[str, dict[str, Any]] = {}

        logger.info("UISettingsManager initialized")

    def load_settings(self) -> None:
        """Load UI settings from persistence."""
        self.ui_settings = self.persistence.load_ui()
        logger.debug("UI settings loaded")

    def save_settings(self) -> None:
        """Save UI settings to persistence."""
        self.persistence.save_ui(self.ui_settings)
        logger.debug("UI settings saved")

    # Window Geometry Management

    def save_window_geometry(self, window: QMainWindow) -> None:
        """Save window geometry and state.

        Args:
            window: Main window to save
        """
        if not self.ui_settings.save_window_geometry:
            return

        try:
            # Save geometry (position and size)
            geometry = window.saveGeometry()

            # Save state (docks and toolbars)
            state = window.saveState()

            # Store in persistence
            self.persistence.save_window_state(
                cast(bytes, geometry.data()), cast(bytes, state.data())
            )

            # Save maximized state separately
            settings = QSettings(self.persistence.organization, self.persistence.application)
            settings.setValue("UI/window_maximized", window.isMaximized())
            settings.setValue("UI/window_fullscreen", window.isFullScreen())

            logger.debug("Window geometry saved")

        except Exception as e:
            logger.error(f"Failed to save window geometry: {e}")

    def restore_window_geometry(self, window: QMainWindow) -> bool:
        """Restore window geometry and state.

        Args:
            window: Main window to restore

        Returns:
            True if restored successfully
        """
        if not self.ui_settings.restore_on_startup:
            return False

        try:
            # Load saved state
            geometry_bytes, state_bytes = self.persistence.load_window_state()

            if not geometry_bytes:
                logger.debug("No saved window geometry found")
                return False

            # Restore geometry
            window.restoreGeometry(QByteArray(geometry_bytes))

            # Restore state if available
            if state_bytes:
                window.restoreState(QByteArray(state_bytes))

            # Restore maximized/fullscreen state
            settings = QSettings(self.persistence.organization, self.persistence.application)

            if settings.value("UI/window_fullscreen", False) == "true":
                window.showFullScreen()
            elif settings.value("UI/window_maximized", False) == "true":
                window.showMaximized()

            logger.debug("Window geometry restored")
            return True

        except Exception as e:
            logger.error(f"Failed to restore window geometry: {e}")
            return False

    def center_window(self, window: QMainWindow, screen: QScreen | None = None) -> None:
        """Center window on screen.

        Args:
            window: Window to center
            screen: Target screen (uses primary if None)
        """
        if not screen:
            screen = window.screen()

        if not screen:
            return

        # Get screen geometry
        screen_rect = screen.availableGeometry()

        # Calculate centered position
        window_rect = window.frameGeometry()
        center_x = (screen_rect.width() - window_rect.width()) // 2
        center_y = (screen_rect.height() - window_rect.height()) // 2

        # Move window
        window.move(screen_rect.x() + center_x, screen_rect.y() + center_y)

    # Panel State Management

    def save_panel_states(self, panels: dict[str, QDockWidget]) -> None:
        """Save states of all panels.

        Args:
            panels: Dictionary of panel_id -> QDockWidget
        """
        if not self.ui_settings.save_panel_layout:
            return

        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.beginGroup("Panels")

        for panel_id, dock in panels.items():
            # Save visibility
            settings.setValue(f"{panel_id}/visible", dock.isVisible())

            # Save floating state
            settings.setValue(f"{panel_id}/floating", dock.isFloating())

            # Save area
            parent = dock.parent()
            if isinstance(parent, QMainWindow):
                area = parent.dockWidgetArea(dock)
                settings.setValue(f"{panel_id}/area", int(cast(int, area)))

            # Save size if floating
            if dock.isFloating():
                settings.setValue(f"{panel_id}/size", dock.size())
                settings.setValue(f"{panel_id}/pos", dock.pos())

            # Cache state
            self._panel_states[panel_id] = {
                "visible": dock.isVisible(),
                "floating": dock.isFloating(),
                "area": int(cast(int, area)) if "area" in locals() else None,
            }

        settings.endGroup()
        logger.debug(f"Saved states for {len(panels)} panels")

    def restore_panel_states(self, panels: dict[str, QDockWidget], window: QMainWindow) -> None:
        """Restore states of all panels.

        Args:
            panels: Dictionary of panel_id -> QDockWidget
            window: Main window for docking
        """
        if not self.ui_settings.restore_on_startup:
            return

        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.beginGroup("Panels")

        for panel_id, dock in panels.items():
            # Restore visibility
            visible = settings.value(f"{panel_id}/visible", True)
            dock.setVisible(visible == "true" if isinstance(visible, str) else bool(visible))

            # Restore floating state
            floating = settings.value(f"{panel_id}/floating", False)
            dock.setFloating(floating == "true" if isinstance(floating, str) else bool(floating))

            # Restore area if not floating
            if not dock.isFloating():
                area = settings.value(f"{panel_id}/area")
                if area:
                    try:
                        dock_area = Qt.DockWidgetArea(int(area))
                        window.addDockWidget(dock_area, dock)
                    except (TypeError, ValueError, RuntimeError) as error:
                        logger.debug("Failed to dock panel %s: %s", panel_id, error)

            # Restore size/position if floating
            else:
                size = settings.value(f"{panel_id}/size")
                if size:
                    dock.resize(size)

                pos = settings.value(f"{panel_id}/pos")
                if pos:
                    dock.move(pos)

        settings.endGroup()
        logger.debug(f"Restored states for {len(panels)} panels")

    def get_panel_visibility(self, panel_id: str) -> bool | None:
        """Get saved panel visibility.

        Args:
            panel_id: Panel identifier

        Returns:
            Visibility state or None if not saved
        """
        settings = QSettings(self.persistence.organization, self.persistence.application)
        value = settings.value(f"Panels/{panel_id}/visible")

        if value is not None:
            return value == "true" if isinstance(value, str) else bool(value)
        return None

    def set_panel_visibility(self, panel_id: str, visible: bool) -> None:
        """Set panel visibility.

        Args:
            panel_id: Panel identifier
            visible: Visibility state
        """
        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.setValue(f"Panels/{panel_id}/visible", visible)

        # Update cache
        if panel_id not in self._panel_states:
            self._panel_states[panel_id] = {}
        self._panel_states[panel_id]["visible"] = visible

    # Toolbar State Management

    def save_toolbar_states(self, toolbars: dict[str, QToolBar]) -> None:
        """Save toolbar states.

        Args:
            toolbars: Dictionary of toolbar_id -> QToolBar
        """
        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.beginGroup("Toolbars")

        for toolbar_id, toolbar in toolbars.items():
            # Save visibility
            settings.setValue(f"{toolbar_id}/visible", toolbar.isVisible())

            # Save area
            parent = toolbar.parent()
            if isinstance(parent, QMainWindow):
                area = parent.toolBarArea(toolbar)
                settings.setValue(f"{toolbar_id}/area", int(cast(int, area)))

            # Save break state
            if isinstance(parent, QMainWindow):
                break_before = parent.toolBarBreak(toolbar)
                settings.setValue(f"{toolbar_id}/break", break_before)

            # Cache state
            self._toolbar_states[toolbar_id] = {
                "visible": toolbar.isVisible(),
                "area": int(cast(int, area)) if "area" in locals() else None,
            }

        settings.endGroup()
        logger.debug(f"Saved states for {len(toolbars)} toolbars")

    def restore_toolbar_states(self, toolbars: dict[str, QToolBar], window: QMainWindow) -> None:
        """Restore toolbar states.

        Args:
            toolbars: Dictionary of toolbar_id -> QToolBar
            window: Main window
        """
        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.beginGroup("Toolbars")

        for toolbar_id, toolbar in toolbars.items():
            # Restore visibility
            visible = settings.value(f"{toolbar_id}/visible", True)
            toolbar.setVisible(visible == "true" if isinstance(visible, str) else bool(visible))

            # Restore area
            area = settings.value(f"{toolbar_id}/area")
            if area:
                try:
                    toolbar_area = Qt.ToolBarArea(int(area))
                    window.addToolBar(toolbar_area, toolbar)
                except (TypeError, ValueError, RuntimeError) as error:
                    logger.debug("Failed to restore toolbar %s: %s", toolbar_id, error)

            # Restore break
            break_before = settings.value(f"{toolbar_id}/break", False)
            if break_before == "true" or break_before is True:
                window.insertToolBarBreak(toolbar)

        settings.endGroup()
        logger.debug(f"Restored states for {len(toolbars)} toolbars")

    # Recent Files Management

    def get_recent_files(self) -> list[str]:
        """Get list of recent files.

        Returns:
            List of recent file paths
        """
        settings = QSettings(self.persistence.organization, self.persistence.application)

        recent = []
        size = settings.beginReadArray("RecentFiles")

        for i in range(min(size, self.ui_settings.max_recent_files)):
            settings.setArrayIndex(i)
            path = settings.value("path")
            if path and Path(path).exists():
                recent.append(path)

        settings.endArray()
        return recent

    def add_recent_file(self, filepath: str) -> None:
        """Add file to recent files list.

        Args:
            filepath: Path to file
        """
        recent = self.get_recent_files()

        # Remove if already in list
        if filepath in recent:
            recent.remove(filepath)

        # Add to front
        recent.insert(0, filepath)

        # Limit size
        recent = recent[: self.ui_settings.max_recent_files]

        # Save
        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.beginWriteArray("RecentFiles")

        for i, path in enumerate(recent):
            settings.setArrayIndex(i)
            settings.setValue("path", path)

        settings.endArray()

    def clear_recent_files(self) -> None:
        """Clear recent files list."""
        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.remove("RecentFiles")

    # Layout Presets

    def save_layout_preset(self, name: str, window: QMainWindow) -> None:
        """Save current layout as preset.

        Args:
            name: Preset name
            window: Main window
        """
        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.beginGroup(f"LayoutPresets/{name}")

        # Save window state
        window_state = window.saveState()
        settings.setValue("state", window_state)

        # Save panel visibility
        for panel_id, panel_state in self._panel_states.items():
            settings.setValue(f"panels/{panel_id}/visible", panel_state.get("visible", True))

        settings.endGroup()
        logger.info(f"Layout preset '{name}' saved")

    def load_layout_preset(
        self, name: str, window: QMainWindow, panels: dict[str, QDockWidget]
    ) -> bool:
        """Load layout preset.

        Args:
            name: Preset name
            window: Main window
            panels: Dictionary of panels

        Returns:
            True if loaded successfully
        """
        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.beginGroup(f"LayoutPresets/{name}")

        # Load window state
        state = settings.value("state")
        if not state:
            settings.endGroup()
            return False

        window.restoreState(state)

        # Load panel visibility
        for panel_id, dock in panels.items():
            visible = settings.value(f"panels/{panel_id}/visible", True)
            dock.setVisible(visible == "true" if isinstance(visible, str) else bool(visible))

        settings.endGroup()
        logger.info(f"Layout preset '{name}' loaded")
        return True

    def get_layout_presets(self) -> list[str]:
        """Get list of saved layout presets.

        Returns:
            List of preset names
        """
        settings = QSettings(self.persistence.organization, self.persistence.application)
        settings.beginGroup("LayoutPresets")
        presets = settings.childGroups()
        settings.endGroup()
        return presets
