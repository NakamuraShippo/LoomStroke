"""Safe detachable tab widget for ComfyUI nodes.

This is a safer implementation that avoids the crash issues with drag continuation.
Uses MovingAwareDock for maximum crash safety.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QTabBar, QTabWidget, QWidget

# Import safety utilities
from loomstroke.ui._dock_safe import WeakBox, is_valid

# Import MovingAwareDock for crash-safe docking
from loomstroke.ui.moving_aware_dock import create_moving_aware_dock

logger = logging.getLogger(__name__)


class SafeDetachableTabBar(QTabBar):
    """Tab bar that supports safe dragging to detach tabs."""

    tab_detach_requested = Signal(int)  # tab_index

    def __init__(self, parent: QWidget | None = None):
        """Initialize safe detachable tab bar."""
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setSelectionBehaviorOnRemove(QTabBar.SelectionBehavior.SelectLeftTab)
        self.setMovable(True)

        self.drag_start_pos = None
        self.drag_index = None
        self.detach_distance = 40  # pixels to drag before detaching

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press to track potential drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.position().toPoint()
            self.drag_index = self.tabAt(self.drag_start_pos)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move to detect detach gesture."""
        if not self.drag_start_pos or self.drag_index < 0:
            super().mouseMoveEvent(event)
            return

        # Calculate drag distance
        drag_distance = (event.position().toPoint() - self.drag_start_pos).manhattanLength()

        # Check for vertical drag (detach gesture)
        drag_vector = event.position().toPoint() - self.drag_start_pos
        is_vertical = abs(drag_vector.y()) > abs(drag_vector.x())

        if is_vertical and drag_distance > self.detach_distance:
            # Request detachment
            self.tab_detach_requested.emit(self.drag_index)
            # Reset drag state
            self.drag_start_pos = None
            self.drag_index = None
        else:
            # Normal tab reordering
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release."""
        self.drag_start_pos = None
        self.drag_index = None
        super().mouseReleaseEvent(event)


class SafeDetachableTabWidget(QTabWidget):
    """Tab widget with safe detachable tabs.

    This implementation avoids the crash issues by:
    1. Not attempting to continue drag after detachment
    2. Using MovingAwareDock for crash-safe native window dragging
    3. Proper cleanup and state management
    4. No pseudo-follow timer that causes crashes
    """

    tab_detached = Signal(int, QDockWidget)  # tab_index, dock_widget

    def __init__(self, parent: QWidget | None = None):
        """Initialize safe detachable tab widget."""
        super().__init__(parent)

        # Use custom tab bar
        self.safe_tab_bar = SafeDetachableTabBar(self)
        self.setTabBar(self.safe_tab_bar)

        # Connect signals
        self.safe_tab_bar.tab_detach_requested.connect(self.detach_tab)

        # Track detached widgets with weak references
        self.detached_docks: dict[int, WeakBox] = {}  # Use WeakBox for safety

        # Configuration
        self.setTabsClosable(False)
        self.setDocumentMode(True)

    def detach_tab(self, index: int) -> None:
        """Safely detach a tab to a floating MovingAwareDock widget.

        Args:
            index: Index of tab to detach
        """
        # Get widget and title
        widget = self.widget(index)
        if not is_valid(widget):
            logger.warning(f"Widget at index {index} is invalid")
            return

        title = self.tabText(index)

        # Find main window
        main_window = self.find_main_window()

        # Create MovingAwareDock for crash-safe dragging
        dock = create_moving_aware_dock(title, widget, main_window)
        dock.setObjectName(f"detached_tab_{index}")

        # Connect move signals for debugging
        dock.native_move_started.connect(
            lambda: logger.debug(f"Tab {index} dock started native move - updates frozen")
        )
        dock.native_move_ended.connect(
            lambda: logger.debug(f"Tab {index} dock ended native move - updates resumed")
        )

        # Remove widget from tab (it's now in the dock)
        self.removeTab(index)

        # Store reference with weak reference
        self.detached_docks[index] = WeakBox(dock)

        # Get cursor position before any window operations
        cursor_pos = QCursor.pos()

        # Add to main window if available
        if is_valid(main_window) and isinstance(main_window, QMainWindow) and is_valid(dock):
            # Add dock to main window (required for proper management)
            main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            # Set as floating immediately
            dock.setFloating(True)

        # Show the dock first
        dock.show()

        # Position at mouse cursor AFTER showing and making it floating
        # This ensures the position is not overridden by Qt's default positioning
        dock.move(cursor_pos.x() - 50, cursor_pos.y() - 10)

        # Bring to front and activate
        dock.raise_()
        dock.activateWindow()

        # Handle close event to potentially return to tabs
        original_close = dock.closeEvent

        def handle_close(event):
            # Option to re-add to tabs
            if is_valid(widget):
                try:
                    self.addTab(widget, title)
                    logger.debug(f"Returned tab {title} from closed dock")
                except Exception as e:
                    logger.debug(f"Could not return tab to widget: {e}")
            if index in self.detached_docks:
                del self.detached_docks[index]
            if callable(original_close):
                original_close(event)

        dock.closeEvent = handle_close

        # Emit signal
        self.tab_detached.emit(index, dock)

        logger.info(f"Tab {index} safely detached as MovingAwareDock '{title}'")

    def find_main_window(self) -> QMainWindow | None:
        """Find the main window.

        Returns:
            Main window or None
        """
        # Walk up parent hierarchy
        parent = self.parent()
        while parent:
            if isinstance(parent, QMainWindow):
                return parent
            parent = parent.parent()

        # Check application windows
        app = QApplication.instance()
        if app:
            for window in app.topLevelWidgets():
                if isinstance(window, QMainWindow):
                    return window

        return None

    def cleanup(self) -> None:
        """Clean up detached docks."""
        for dock in self.detached_docks.values():
            if dock:
                try:
                    dock.close()
                except Exception as e:
                    logger.debug(f"Error closing detached dock: {e}")
        self.detached_docks.clear()
        logger.debug("Cleaned up all detached docks")
