"""Enhanced detachable tabs with visual docking feedback.

This module extends safe_detach_tabs.py with visual indicators for docking zones.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QTabBar, QTabWidget, QWidget

# Import safety utilities
from loomstroke.ui._dock_safe import WeakBox, is_valid, safe_widget_operation
from loomstroke.ui.dock_hint_overlay import DockHintOverlay
from loomstroke.ui.dock_hit_test import get_drop_area, hit_test

logger = logging.getLogger(__name__)


class VisualDetachableTabBar(QTabBar):
    """Tab bar with visual feedback for dragging and docking."""

    tab_detach_requested = Signal(int, QPoint)  # tab_index, global_pos
    tab_drag_started = Signal(int)  # tab_index
    tab_drag_ended = Signal()

    def __init__(self, parent: QWidget | None = None):
        """Initialize visual detachable tab bar."""
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setSelectionBehaviorOnRemove(QTabBar.SelectionBehavior.SelectLeftTab)
        self.setMovable(True)

        # Drag tracking
        self.drag_start_pos = None
        self.drag_index = None
        self.is_dragging = False
        self.detach_distance = 40  # pixels to drag before detaching
        self.preview_distance = 20  # pixels to start showing preview

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press to track potential drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.position().toPoint()
            self.drag_index = self.tabAt(self.drag_start_pos)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle mouse move to detect detach gesture with visual feedback."""
        if not self.drag_start_pos or self.drag_index < 0:
            super().mouseMoveEvent(event)
            return

        # Calculate drag distance
        drag_distance = (event.position().toPoint() - self.drag_start_pos).manhattanLength()

        # Check for vertical drag (detach gesture)
        drag_vector = event.position().toPoint() - self.drag_start_pos
        is_vertical = abs(drag_vector.y()) > abs(drag_vector.x())

        # Start visual feedback before actual detachment
        if not self.is_dragging and drag_distance > self.preview_distance:
            self.is_dragging = True
            self.tab_drag_started.emit(self.drag_index)

        if is_vertical and drag_distance > self.detach_distance:
            # Request detachment with global position
            global_pos = event.globalPosition().toPoint()
            self.tab_detach_requested.emit(self.drag_index, global_pos)
            # Reset drag state
            self.drag_start_pos = None
            self.drag_index = None
            self.is_dragging = False
            self.tab_drag_ended.emit()
        else:
            # Normal tab reordering
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle mouse release."""
        if self.is_dragging:
            self.tab_drag_ended.emit()
        self.drag_start_pos = None
        self.drag_index = None
        self.is_dragging = False
        super().mouseReleaseEvent(event)


class FloatingDockController:
    """Controls floating dock with visual docking feedback."""

    def __init__(self, dock: QDockWidget, main_window: QMainWindow):
        """Initialize dock controller.

        Args:
            dock: The floating dock widget
            main_window: Main window for docking
        """
        self.dock = WeakBox(dock)
        self.main_window = WeakBox(main_window)
        self.overlay = None
        self.last_hit_info = {"kind": None, "target": None, "rect": None}
        self.drag_offset = QPoint()
        self.is_dragging = False

        # Create overlay for visual feedback
        if is_valid(main_window):
            self.overlay = DockHintOverlay(main_window)

        # Install event filter on dock's title bar
        if is_valid(dock):
            dock.installEventFilter(self)

    def eventFilter(self, obj, event):
        """Filter events for dock dragging with visual feedback."""
        dock = self.dock.get()
        main_window = self.main_window.get()

        if not is_valid(dock) or not is_valid(main_window):
            return False

        if event.type() == event.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                # Start drag tracking
                self.is_dragging = True
                self.drag_offset = event.globalPosition().toPoint() - dock.pos()
                return False

        elif event.type() == event.MouseMove:
            if self.is_dragging and dock.isFloating():
                # Update visual feedback
                global_pos = event.globalPosition().toPoint()
                self._update_visual_feedback(global_pos)
                return False

        elif (
            event.type() == event.MouseButtonRelease
            and event.button() == Qt.LeftButton
            and self.is_dragging
        ):
            # Perform docking if in a drop zone
            self._perform_docking()
            self.is_dragging = False
            if self.overlay:
                self.overlay.clear()
            return False

        return False

    def _update_visual_feedback(self, global_pos: QPoint):
        """Update visual feedback for docking zones.

        Args:
            global_pos: Current global cursor position
        """
        main_window = self.main_window.get()
        if not is_valid(main_window) or not self.overlay:
            return

        # Perform hit test
        hit_info = hit_test(main_window, global_pos)
        self.last_hit_info = hit_info

        # Update overlay
        if hit_info["kind"]:
            target_type = "tab" if hit_info["kind"] == "tab" else "edge"
            self.overlay.show_rect(hit_info["rect"], target_type)
        else:
            self.overlay.clear()

    def _perform_docking(self):
        """Perform docking based on last hit test."""
        dock = self.dock.get()
        main_window = self.main_window.get()

        if not is_valid(dock) or not is_valid(main_window):
            return

        hit_info = self.last_hit_info
        if not hit_info["kind"]:
            return  # No valid drop zone

        # Disable updates during docking
        main_window.setUpdatesEnabled(False)

        try:
            if hit_info["kind"] == "tab" and is_valid(hit_info["target"]):
                # Tabify with target dock
                dock.setFloating(False)
                main_window.tabifyDockWidget(hit_info["target"], dock)
                logger.debug(f"Docked as tab with {hit_info['target'].windowTitle()}")

            elif hit_info["kind"] in ("left", "right", "top", "bottom"):
                # Dock to edge
                area = get_drop_area(hit_info["kind"])
                if area:
                    dock.setFloating(False)
                    main_window.addDockWidget(area, dock)
                    logger.debug(f"Docked to {hit_info['kind']} edge")

            elif hit_info["kind"] == "center":
                # Keep floating but center it
                center = main_window.rect().center()
                global_center = main_window.mapToGlobal(center)
                dock.move(
                    global_center.x() - dock.width() // 2, global_center.y() - dock.height() // 2
                )
                logger.debug("Centered floating dock")

        except Exception as e:
            logger.error(f"Error during docking: {e}")

        finally:
            # Re-enable updates after a short delay
            QTimer.singleShot(
                0, lambda: safe_widget_operation(main_window, lambda w: w.setUpdatesEnabled(True))
            )

    def cleanup(self):
        """Clean up controller resources."""
        if self.overlay:
            self.overlay.clear()
            self.overlay.deleteLater()
            self.overlay = None


class VisualDetachableTabWidget(QTabWidget):
    """Tab widget with visual docking feedback.

    This extends the safe implementation with:
    1. Visual drop zone indicators during drag
    2. Real-time docking preview
    3. Smooth docking on drop
    """

    tab_detached = Signal(int, QDockWidget)  # tab_index, dock_widget

    def __init__(self, parent: QWidget | None = None):
        """Initialize visual detachable tab widget."""
        super().__init__(parent)

        # Use custom tab bar with visual feedback
        self.visual_tab_bar = VisualDetachableTabBar(self)
        self.setTabBar(self.visual_tab_bar)

        # Connect signals
        self.visual_tab_bar.tab_detach_requested.connect(self.detach_tab_with_feedback)
        self.visual_tab_bar.tab_drag_started.connect(self.on_drag_started)
        self.visual_tab_bar.tab_drag_ended.connect(self.on_drag_ended)

        # Track detached widgets and their controllers
        self.detached_docks: dict[int, WeakBox] = {}
        self.dock_controllers: dict[int, FloatingDockController] = {}

        # Configuration
        self.setTabsClosable(False)
        self.setDocumentMode(True)

        # Visual feedback overlay for tab dragging
        self.tab_drag_overlay = None

    def on_drag_started(self, index: int):
        """Handle drag start for visual feedback.

        Args:
            index: Tab index being dragged
        """
        logger.debug(f"Started dragging tab {index}")

        # Create overlay for visual feedback if main window available
        main_window = self.find_main_window()
        if is_valid(main_window) and not self.tab_drag_overlay:
            self.tab_drag_overlay = DockHintOverlay(main_window)

    def on_drag_ended(self):
        """Handle drag end to clear visual feedback."""
        logger.debug("Ended tab dragging")

        # Clear overlay
        if self.tab_drag_overlay:
            self.tab_drag_overlay.clear()

    def detach_tab_with_feedback(self, index: int, global_pos: QPoint):
        """Detach tab with visual docking feedback.

        Args:
            index: Index of tab to detach
            global_pos: Global position where tab was detached
        """
        # Clear any tab drag overlay
        if self.tab_drag_overlay:
            self.tab_drag_overlay.clear()

        # Get widget and title
        widget = self.widget(index)
        if not is_valid(widget):
            return

        title = self.tabText(index)

        # Find main window
        main_window = self.find_main_window()

        # Create dock widget with safety checks
        dock = QDockWidget(title, main_window if is_valid(main_window) else None)
        if is_valid(widget):
            dock.setWidget(widget)
        else:
            logger.warning(f"Widget became invalid during detach for tab {index}")
            return
        dock.setObjectName(f"detached_tab_{index}")

        # Configure dock
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        # Remove widget from tab (it's now in the dock)
        self.removeTab(index)

        # Store reference with weak reference
        self.detached_docks[index] = WeakBox(dock)

        # Add to main window if available
        if is_valid(main_window) and isinstance(main_window, QMainWindow) and is_valid(dock):
            # Add dock to main window (required for proper management)
            main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            # Set as floating immediately
            dock.setFloating(True)

            # Create controller for visual feedback
            controller = FloatingDockController(dock, main_window)
            self.dock_controllers[index] = controller

        # Show the dock first
        dock.show()

        # Position at mouse cursor
        dock.move(global_pos.x() - 50, global_pos.y() - 10)

        # Bring to front and activate
        dock.raise_()
        dock.activateWindow()

        # Handle close event to potentially return to tabs
        original_close = dock.closeEvent

        def handle_close(event):
            # Clean up controller
            if index in self.dock_controllers:
                self.dock_controllers[index].cleanup()
                del self.dock_controllers[index]

            # Option to re-add to tabs
            if widget:
                self.addTab(widget, title)
            if index in self.detached_docks:
                del self.detached_docks[index]
            original_close(event)

        dock.closeEvent = handle_close

        # Emit signal
        self.tab_detached.emit(index, dock)

        logger.debug(f"Tab {index} detached with visual feedback as dock '{title}'")

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

    def cleanup(self):
        """Clean up detached docks and controllers."""
        # Clean up controllers
        for controller in self.dock_controllers.values():
            controller.cleanup()
        self.dock_controllers.clear()

        # Close detached docks
        for dock_ref in self.detached_docks.values():
            dock = dock_ref.get() if hasattr(dock_ref, "get") else None
            if dock and is_valid(dock):
                dock.close()
        self.detached_docks.clear()

        # Clean up overlays
        if self.tab_drag_overlay:
            self.tab_drag_overlay.clear()
            self.tab_drag_overlay.deleteLater()
            self.tab_drag_overlay = None
