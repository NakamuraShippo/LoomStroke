"""
DockAnimationBlocker - Disable Qt's internal docking animations and previews.

This module provides utilities to completely disable Qt's internal docking
animations, preview widgets, and hover effects that can cause crashes during
drag operations.
"""

from __future__ import annotations

import logging
from typing import cast

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QRubberBand, QWidget

logger = logging.getLogger(__name__)


class DockAnimationBlocker(QObject):
    """
    Event filter that blocks Qt's internal docking animations and previews.

    Qt creates internal preview widgets and animations during dock operations
    that cannot be controlled through public API. This filter blocks them.
    """

    def __init__(self) -> None:
        """Initialize DockAnimationBlocker."""
        super().__init__()

        # Track state
        self.active = False
        self.blocked_count = 0

        # Widget classes to block during drag
        self.blocked_classes = {
            "QDockWidgetGroupWindow",  # Qt's internal dock group window
            "QDockAreaLayoutInfo",  # Qt's internal dock area layout
            "QDockWidgetLayout",  # Qt's internal dock layout
            "QMainWindowLayoutState",  # Qt's internal layout state
        }

        logger.debug("DockAnimationBlocker initialized")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Filter events to block internal docking animations.

        Args:
            obj: Object receiving the event
            event: Event to filter

        Returns:
            True to block the event, False to let it through
        """
        if not self.active:
            return False

        # Get event type
        event_type = event.type()

        # Block internal dock widget creation during drag
        if event_type == QEvent.Type.ChildAdded and isinstance(obj, QWidget):
            class_name = obj.__class__.__name__

            # Check if this is an internal Qt docking class
            if any(blocked in class_name for blocked in self.blocked_classes):
                self.blocked_count += 1
                logger.debug(f"Blocked internal dock widget creation: {class_name}")
                return True

            # Also block if parent is QMainWindow and child is QRubberBand
            # (Qt's internal dock preview)
            child = getattr(event, "child", None)
            if isinstance(obj, QMainWindow) and isinstance(child, QRubberBand):
                self.blocked_count += 1
                logger.debug("Blocked Qt internal dock preview QRubberBand")
                return True

        # Block hover events on dock widgets during drag
        # These trigger Qt's internal animations
        if (
            event_type in [QEvent.Type.HoverEnter, QEvent.Type.HoverMove, QEvent.Type.HoverLeave]
            and isinstance(obj, QWidget)
            and "Dock" in obj.__class__.__name__
        ):
            self.blocked_count += 1
            if self.blocked_count % 10 == 0:
                logger.debug(f"Blocked {self.blocked_count} hover events on dock widgets")
            return True

        # Block Polish events on dock widgets (Qt uses these for animations)
        if (
            event_type == QEvent.Type.Polish
            and isinstance(obj, QWidget)
            and "Dock" in obj.__class__.__name__
        ):
            self.blocked_count += 1
            return True

        return False

    def set_active(self, active: bool) -> None:
        """Enable or disable blocking.

        Args:
            active: True to enable blocking, False to disable
        """
        if self.active == active:
            return

        self.active = active

        if active:
            logger.debug("DockAnimationBlocker activated")
            self.blocked_count = 0
        else:
            logger.debug(f"DockAnimationBlocker deactivated - blocked {self.blocked_count} events")


def disable_dock_animations(main_window: QMainWindow) -> None:
    """Disable all Qt internal dock animations for a main window.

    This function applies multiple techniques to prevent Qt's internal
    docking animations from interfering with custom implementations.

    Args:
        main_window: QMainWindow to configure
    """
    # Disable dock options that trigger animations
    current_options = main_window.dockOptions()

    # Remove AnimatedDocks if present
    if current_options & QMainWindow.DockOption.AnimatedDocks:
        current_options &= ~QMainWindow.DockOption.AnimatedDocks
        main_window.setDockOptions(current_options)
        logger.info("Disabled AnimatedDocks option")

    # Disable dock widget animations via style hints
    # This prevents Qt from using fade/slide animations
    main_window.setStyleSheet("""
        QMainWindow::separator {
            background: palette(dark);
            width: 1px;
            height: 1px;
        }
        QDockWidget {
            /* Disable animations */
            animation-duration: 0ms;
        }
        QDockWidget::title {
            /* Disable title bar animations */
            animation-duration: 0ms;
        }
    """)

    # Set attributes to reduce animation overhead
    main_window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
    main_window.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    logger.debug("Applied dock animation disabling configurations")


def install_animation_blocker(app: QApplication | None = None) -> DockAnimationBlocker:
    """Install a global dock animation blocker.

    Args:
        app: QApplication instance, or None to get current

    Returns:
        The installed DockAnimationBlocker instance
    """
    if app is None:
        app = cast(QApplication | None, QApplication.instance())

    if not app:
        raise RuntimeError("No QApplication instance available")

    # Create blocker
    blocker = DockAnimationBlocker()

    # Install as event filter
    app.installEventFilter(blocker)

    logger.info("Global dock animation blocker installed")

    return blocker
