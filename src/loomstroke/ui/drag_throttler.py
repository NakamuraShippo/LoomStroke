"""
DragThrottler - Global event filter to block paint/update events during dock dragging.

This module provides a global event filter that intercepts and discards paint,
update, layout, and polish events during dock dragging operations to prevent
crashes from excessive repainting.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any, cast

from PySide6.QtCore import QAbstractAnimation, QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)


class DragThrottler(QObject):
    """
    Global event filter that blocks paint/update events during drag operations.

    This prevents excessive repainting and Qt internal conflicts during
    native window move operations.
    """

    def __init__(self, overlay_whitelist: set[QWidget] | None = None):
        """Initialize DragThrottler.

        Args:
            overlay_whitelist: Set of widgets (like overlays) that should
                              still receive paint events during throttling
        """
        super().__init__()

        # Throttling state
        self.active = False

        # Widgets that should still paint (overlays, indicators)
        self.whitelist = overlay_whitelist or set()

        # Event types to block during drag
        self.blocked_events = {
            QEvent.Type.UpdateRequest,
            QEvent.Type.Paint,
            QEvent.Type.LayoutRequest,
            QEvent.Type.PolishRequest,
            QEvent.Type.UpdateLater,
            QEvent.Type.Show,  # Block show events during drag
        }

        # Statistics for debugging
        self._blocked_count = 0
        self._allowed_count = 0

        logger.debug("DragThrottler initialized")

    def add_to_whitelist(self, widget: QWidget) -> None:
        """Add a widget to the whitelist.

        Args:
            widget: Widget that should always receive events
        """
        self.whitelist.add(widget)
        logger.debug(f"Added to whitelist: {widget.objectName() or widget.__class__.__name__}")

    def remove_from_whitelist(self, widget: QWidget) -> None:
        """Remove a widget from the whitelist.

        Args:
            widget: Widget to remove from whitelist
        """
        self.whitelist.discard(widget)
        logger.debug(f"Removed from whitelist: {widget.objectName() or widget.__class__.__name__}")

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Filter events globally."""
        if not self.active:
            return False

        event_type = event.type()
        if event_type not in self.blocked_events:
            return False

        if not isinstance(obj, QWidget):
            return False

        widget = obj
        if self._is_whitelisted(widget):
            return False

        if self._should_allow_title_widget(widget):
            return False

        self._blocked_count += 1
        if self._blocked_count % 100 == 0:
            logger.debug("Blocked %s events, allowed %s", self._blocked_count, self._allowed_count)
        return True

    def _is_whitelisted(self, widget: QWidget) -> bool:
        """Return True if the widget or its ancestors are whitelisted."""
        for whitelisted in list(self.whitelist):
            try:
                if widget is whitelisted or whitelisted.isAncestorOf(widget):
                    self._allowed_count += 1
                    return True
            except (RuntimeError, AttributeError):
                continue
        return False

    def _should_allow_title_widget(self, widget: QWidget) -> bool:
        """Return True if the widget should always be allowed to paint."""
        widget_name = (widget.objectName() or "").lower()
        widget_class = widget.__class__.__name__
        if any(token in widget_name for token in ("titlebar", "frame", "caption")):
            self._allowed_count += 1
            return True
        if any(token in widget_class for token in ("TitleBar", "Frame", "Caption", "QDockWidget")):
            self._allowed_count += 1
            return True
        return False

    def set_active(self, active: bool) -> None:
        """Enable or disable throttling.

        Args:
            active: True to enable throttling, False to disable
        """
        if self.active == active:
            return

        self.active = active

        if active:
            logger.debug("DragThrottler activated - blocking paint events")
            self._blocked_count = 0
            self._allowed_count = 0
        else:
            logger.debug(
                "DragThrottler deactivated - blocked %s, allowed %s",
                self._blocked_count,
                self._allowed_count,
            )

            # Force a global update after throttling ends
            self._force_global_update()

    def _force_global_update(self) -> None:
        """Force a global update after throttling ends."""
        app = cast(QApplication | None, QApplication.instance())
        if not app:
            return

        # Update all top-level widgets
        for widget in app.topLevelWidgets():
            with suppress(Exception):
                if widget and widget.isVisible():
                    widget.update()

        # Process events to ensure updates are applied
        app.processEvents(cast(Any, QEvent.Type.UpdateRequest))


class AnimationFreezer:
    """
    Helper class to pause/resume animations during drag operations.
    """

    def __init__(self) -> None:
        """Initialize AnimationFreezer."""
        self.paused_animations: list[Any] = []
        logger.debug("AnimationFreezer initialized")

    def freeze_animations(self, root_widget: QWidget | None = None) -> None:
        """Pause all animations under a widget tree.

        Args:
            root_widget: Root widget to search under, or None for all
        """
        self.paused_animations.clear()

        # Get search root
        app = cast(QApplication | None, QApplication.instance())
        if not app:
            return

        widgets = [root_widget] if root_widget else app.topLevelWidgets()

        # Find and pause animations
        for widget in widgets:
            try:
                animations = widget.findChildren(QAbstractAnimation)
                for anim in animations:
                    if anim.state() == QAbstractAnimation.State.Running:
                        anim.pause()
                        self.paused_animations.append(anim)
                        name = anim.objectName() or anim.__class__.__name__
                        logger.debug("Paused animation: %s", name)
            except Exception as e:
                logger.debug("Error pausing animations: %s", e)

        if self.paused_animations:
            logger.debug(f"Paused {len(self.paused_animations)} animations")

    def resume_animations(self) -> None:
        """Resume all previously paused animations."""
        resumed_count = 0
        for anim in self.paused_animations:
            try:
                if anim.state() == QAbstractAnimation.State.Paused:
                    anim.resume()
                    resumed_count += 1
            except Exception as e:
                logger.debug("Error resuming animation: %s", e)

        if resumed_count:
            logger.debug("Resumed %s animations", resumed_count)

        self.paused_animations.clear()


def install_drag_throttler(
    app: QApplication | None = None, overlay_widgets: set[QWidget] | None = None
) -> DragThrottler:
    """Install a global drag throttler on the application.

    Args:
        app: QApplication instance, or None to get current
        overlay_widgets: Widgets that should still paint during throttling

    Returns:
        The installed DragThrottler instance
    """
    if app is None:
        app = cast(QApplication | None, QApplication.instance())

    if not app:
        raise RuntimeError("No QApplication instance available")

    # Create throttler
    throttler = DragThrottler(overlay_widgets)

    # Install as global event filter
    app.installEventFilter(throttler)

    logger.info("Global drag throttler installed")

    return throttler
