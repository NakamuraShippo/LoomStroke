"""
MovingAwareDock - Windows native move loop detection and update freezing.

This module provides a QDockWidget subclass that detects Windows WM_ENTERSIZEMOVE
and WM_EXITSIZEMOVE messages to freeze widget updates during native dragging,
preventing Access Violations and crashes.
"""

from __future__ import annotations

import logging
import sys
from contextlib import suppress

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QMoveEvent, QResizeEvent
from PySide6.QtWidgets import QApplication, QDockWidget, QGraphicsView, QWidget

# Windows-specific imports for native message detection
if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes

        # Windows message constants
        WM_ENTERSIZEMOVE = 0x0231
        WM_EXITSIZEMOVE = 0x0232
        WM_MOVING = 0x0216
        WM_SIZING = 0x0214

        # Windows API functions
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        WINDOWS_NATIVE_SUPPORT = True
    except (ImportError, AttributeError):
        WINDOWS_NATIVE_SUPPORT = False
        WM_ENTERSIZEMOVE = WM_EXITSIZEMOVE = WM_MOVING = WM_SIZING = 0
else:
    WINDOWS_NATIVE_SUPPORT = False
    WM_ENTERSIZEMOVE = WM_EXITSIZEMOVE = WM_MOVING = WM_SIZING = 0

logger = logging.getLogger(__name__)


class MovingAwareDock(QDockWidget):
    """
    QDockWidget that detects Windows native move loops and freezes updates.

    This prevents crashes caused by Qt repaint/geometry conflicts during
    native window dragging operations.
    """

    # Signals for move state changes
    nativeMoveActiveChanged = Signal(bool)
    native_move_started = Signal()
    native_move_ended = Signal()

    def __init__(self, title: str = "", parent: QWidget | None = None):
        """Initialize MovingAwareDock.

        Args:
            title: Dock window title
            parent: Parent widget (typically QMainWindow)
        """
        super().__init__(title, parent)

        # Track native move state
        self._in_native_move = False
        self._drag_active = False
        self._pending_state: bool | None = None  # None/True/False
        self._frozen_widgets: list[QWidget] = []
        self._graphics_views: list[QGraphicsView] = []

        # Configure for optimal floating behavior
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        # Don't enable WA_NativeWindow during moves - causes WinId regeneration crashes
        # self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)  # REMOVED

        # Don't connect to floating state changes - avoid runtime attribute changes
        # self.topLevelChanged.connect(self._on_floating_changed)  # REMOVED

        logger.debug(f"MovingAwareDock created with native support: {WINDOWS_NATIVE_SUPPORT}")

    # Removed _on_floating_changed - runtime WA_NativeWindow changes cause crashes

    def nativeEvent(
        self, eventType: QByteArray | bytes | bytearray | memoryview, message: int
    ) -> tuple[bool, int]:
        """Handle Windows native events for move detection.

        IMPORTANT: Only schedule state changes, never apply them directly!
        Direct widget manipulation during nativeEvent causes reentrancy crashes.

        Args:
            eventType: Event type identifier
            message: Native message data

        Returns:
            Tuple of (handled, result)
        """
        handled = False

        if not WINDOWS_NATIVE_SUPPORT or not self.isFloating():
            return (handled, 0)

        try:
            # Decode Windows message
            if eventType == b"windows_generic_MSG":
                msg = ctypes.cast(message, ctypes.POINTER(wintypes.MSG)).contents

                if msg.message == WM_ENTERSIZEMOVE:
                    # DO NOT call _enter_native_move() directly!
                    self._schedule_drag_state(True)
                    logger.debug("Detected WM_ENTERSIZEMOVE - scheduling freeze")

                elif msg.message == WM_EXITSIZEMOVE:
                    # DO NOT call _exit_native_move() directly!
                    self._schedule_drag_state(False)
                    logger.debug("Detected WM_EXITSIZEMOVE - scheduling resume")

        except Exception as e:
            logger.debug(f"Error in nativeEvent: {e}")

        return (handled, 0)

    def _schedule_drag_state(self, state: bool) -> None:
        """Schedule drag state change for next event loop iteration.

        This avoids reentrancy issues when called from nativeEvent.

        Args:
            state: True to enter drag, False to exit
        """
        # Skip if same state already pending
        if self._pending_state is state:
            return

        self._pending_state = state
        QTimer.singleShot(0, self._apply_drag_state_if_pending)

    def _apply_drag_state_if_pending(self) -> None:
        """Apply pending drag state change.

        Called from timer after returning to event loop.
        """
        if self._pending_state is None:
            return

        state = self._pending_state
        self._pending_state = None

        # Skip if already in target state
        if self._drag_active == state:
            return

        self._drag_active = state

        # Apply the state change
        if state:
            self._enter_native_move()
        else:
            self._exit_native_move()

        # Emit new signal for external control
        self.nativeMoveActiveChanged.emit(state)

    def moveEvent(self, event: QMoveEvent) -> None:
        """Handle move events with fallback move detection.

        This provides fallback move detection for non-Windows platforms
        or when native event detection fails.

        Args:
            event: Move event
        """
        # Fallback move detection if native events not available
        if not WINDOWS_NATIVE_SUPPORT and self.isFloating() and not self._in_native_move:
            self._enter_native_move()

        super().moveEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Handle resize events with fallback detection.

        Args:
            event: Resize event
        """
        # Fallback for resize operations
        if not WINDOWS_NATIVE_SUPPORT and self.isFloating() and not self._in_native_move:
            self._enter_native_move()

        super().resizeEvent(event)

    def _enter_native_move(self) -> None:
        """Enter native move mode - freeze updates."""
        if self._in_native_move:
            return

        self._in_native_move = True
        self._freeze_widget_updates()
        self.native_move_started.emit()

        logger.debug("Entered native move mode - updates frozen")

    def _exit_native_move(self) -> None:
        """Exit native move mode - resume updates."""
        if not self._in_native_move:
            return

        self._in_native_move = False
        self._resume_widget_updates()
        self.native_move_ended.emit()

        logger.debug("Exited native move mode - updates resumed")

    def _freeze_widget_updates(self) -> None:
        """Freeze updates on heavy widgets during move."""
        self._frozen_widgets.clear()
        self._graphics_views.clear()

        # Find all child widgets and freeze heavy ones
        widget = self.widget()
        if widget:
            self._freeze_widget_recursive(widget)

        logger.debug(
            "Froze %s widgets, %s graphics views",
            len(self._frozen_widgets),
            len(self._graphics_views),
        )

    def _freeze_widget_recursive(self, widget: QWidget) -> None:
        """Recursively freeze widget updates.

        NOTE: This is now called from the timer callback, not directly from nativeEvent.

        Args:
            widget: Widget to examine and potentially freeze
        """
        if not widget:
            return

        try:
            # Freeze QGraphicsView widgets (heavy repainting)
            if isinstance(widget, QGraphicsView):
                widget.setUpdatesEnabled(False)
                # Also set viewport update mode for graphics views
                widget.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.NoViewportUpdate)
                self._graphics_views.append(widget)
                logger.debug(f"Froze QGraphicsView: {widget.objectName()}")

            # Freeze other potentially heavy widgets
            elif hasattr(widget, "viewport") and callable(widget.viewport):
                # Widgets with viewports (scroll areas, etc.)
                widget.setUpdatesEnabled(False)
                self._frozen_widgets.append(widget)
                logger.debug(f"Froze viewport widget: {widget.objectName()}")

            # Recursively check children
            for child in widget.findChildren(QWidget):
                if isinstance(child, QGraphicsView):
                    child.setUpdatesEnabled(False)
                    child.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.NoViewportUpdate)
                    self._graphics_views.append(child)
                elif hasattr(child, "viewport") and callable(child.viewport):
                    child.setUpdatesEnabled(False)
                    self._frozen_widgets.append(child)

        except Exception as e:
            logger.debug(f"Error freezing widget {widget}: {e}")

    def _resume_widget_updates(self) -> None:
        """Resume updates on previously frozen widgets."""
        # Resume QGraphicsView updates
        for view in self._graphics_views:
            with suppress(Exception):
                if view and hasattr(view, "setUpdatesEnabled"):
                    view.setUpdatesEnabled(True)
                    # Restore smart viewport update mode
                    view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
                    # Force a single repaint after resume
                    view.update()

        # Resume other widget updates
        for widget in self._frozen_widgets:
            with suppress(Exception):
                if widget and hasattr(widget, "setUpdatesEnabled"):
                    widget.setUpdatesEnabled(True)
                    widget.update()

        # Clear tracking lists
        self._graphics_views.clear()
        self._frozen_widgets.clear()

        logger.debug("Resumed all frozen widget updates")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle close event with proper cleanup.

        Args:
            event: Close event
        """
        # Ensure updates are resumed before closing
        if self._in_native_move:
            self._exit_native_move()

        super().closeEvent(event)

    def setWidget(self, widget: QWidget) -> None:
        """Override setWidget to monitor for heavy widgets.

        Args:
            widget: Widget to set as dock content
        """
        super().setWidget(widget)

        # Log widget type for debugging
        if widget:
            widget_type = type(widget).__name__
            has_graphics = bool(widget.findChildren(QGraphicsView))
            logger.debug(f"Dock widget set: {widget_type}, has graphics: {has_graphics}")


def create_moving_aware_dock(
    title: str,
    widget: QWidget,
    parent: QWidget | None = None,
) -> MovingAwareDock:
    """Create a MovingAwareDock with proper configuration.

    Args:
        title: Dock window title
        widget: Widget to place in dock
        parent: Parent widget (typically QMainWindow)

    Returns:
        Configured MovingAwareDock instance
    """
    dock = MovingAwareDock(title, parent)
    dock.setWidget(widget)

    # Configure standard dock features
    dock.setFeatures(
        QDockWidget.DockWidgetFeature.DockWidgetMovable
        | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        | QDockWidget.DockWidgetFeature.DockWidgetClosable
    )

    # Allow docking to any area
    dock.setAllowedAreas(
        Qt.DockWidgetArea.LeftDockWidgetArea
        | Qt.DockWidgetArea.RightDockWidgetArea
        | Qt.DockWidgetArea.TopDockWidgetArea
        | Qt.DockWidgetArea.BottomDockWidgetArea
    )

    logger.debug(f"Created MovingAwareDock: {title}")
    return dock


def enable_move_aware_application() -> None:
    """Enable move-aware behavior for the entire application.

    This sets global application attributes that can help with
    dock widget performance and stability.
    """
    app = QApplication.instance()
    if not app:
        logger.warning("No QApplication instance found")
        return

    # Enable attributes that improve dock widget performance
    if WINDOWS_NATIVE_SUPPORT:
        disable_attr = getattr(Qt.ApplicationAttribute, "AA_DisableWindowContextHelpButton", None)
        siblings_attr = getattr(Qt.ApplicationAttribute, "AA_DontCreateNativeWidgetSiblings", None)
        if disable_attr is not None:
            app.setAttribute(disable_attr, True)
        if siblings_attr is not None:
            app.setAttribute(siblings_attr, False)
        logger.debug("Enabled application-level dock widget optimizations")
    else:
        logger.debug("Windows native support not available - using fallback mode")
