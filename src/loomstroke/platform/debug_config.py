"""
Debug configuration for controlling verbose logging.

This module provides centralized control over debug logging
to reduce log noise in production environments.
"""

from __future__ import annotations

import logging
import os
from typing import Any

# Check environment variable for debug mode
DEBUG_MODE = os.getenv("LOOMSTROKE_DEBUG", "").lower() in ("1", "true", "yes")

# Debug flags for specific components
DEBUG_FLAGS = {
    "dock": False,  # Dock/undock operations
    "drag": False,  # Drag and drop operations
    "animation": False,  # Animation systems
    "crash": False,  # Crash logging (keep minimal info only)
    "ui": False,  # General UI operations
    "connection": False,  # Network connections
}

# Override all flags if DEBUG_MODE is enabled
if DEBUG_MODE:
    for key in DEBUG_FLAGS:
        DEBUG_FLAGS[key] = True


def is_debug_enabled(component: str) -> bool:
    """Check if debug logging is enabled for a component.

    Args:
        component: Component name to check

    Returns:
        True if debug logging is enabled for the component
    """
    return DEBUG_FLAGS.get(component, DEBUG_MODE)


def configure_logging(level: int | None = None) -> None:
    """Configure logging levels for the application.

    Args:
        level: Optional logging level override
    """
    if level is None:
        level = logging.DEBUG if DEBUG_MODE else logging.INFO

    # Set root logger level
    logging.getLogger().setLevel(level)

    # Suppress verbose loggers unless in debug mode
    if not DEBUG_MODE:
        # Suppress Qt internal debug messages
        logging.getLogger("qt").setLevel(logging.WARNING)

        # Suppress dock/drag related debug messages
        logging.getLogger("loomstroke.ui.moving_aware_dock").setLevel(logging.WARNING)
        logging.getLogger("loomstroke.ui.drag_throttler").setLevel(logging.WARNING)
        logging.getLogger("loomstroke.ui.dock_animation_blocker").setLevel(logging.WARNING)
        logging.getLogger("loomstroke.ui._dock_safe").setLevel(logging.WARNING)

        # Suppress ComfyUI UI debug messages
        logging.getLogger("loomstroke.plugins.comfyui.ui").setLevel(logging.WARNING)

        # Keep essential info for main components
        logging.getLogger("loomstroke.app").setLevel(logging.INFO)
        logging.getLogger("loomstroke.core").setLevel(logging.INFO)
        logging.getLogger("loomstroke.platform").setLevel(logging.INFO)


class ConditionalDebugLogger:
    """Wrapper for conditional debug logging based on component flags."""

    def __init__(self, logger: logging.Logger, component: str):
        """Initialize conditional logger.

        Args:
            logger: Underlying logger instance
            component: Component name for flag checking
        """
        self.logger = logger
        self.component = component

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log debug message only if component debugging is enabled.

        Args:
            msg: Message to log
            *args: Format arguments
            **kwargs: Additional logging arguments
        """
        if is_debug_enabled(self.component):
            self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Always log info messages."""
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Always log warning messages."""
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Always log error messages."""
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Always log critical messages."""
        self.logger.critical(msg, *args, **kwargs)


def get_logger(name: str, component: str | None = None) -> ConditionalDebugLogger:
    """Get a conditional logger for a module.

    Args:
        name: Logger name (usually __name__)
        component: Optional component name for debug flag checking

    Returns:
        ConditionalDebugLogger instance
    """
    logger = logging.getLogger(name)

    # Extract component from name if not provided
    if component is None:
        if "dock" in name.lower():
            component = "dock"
        elif "drag" in name.lower():
            component = "drag"
        elif "animation" in name.lower():
            component = "animation"
        elif "ui" in name.lower():
            component = "ui"
        else:
            component = "general"

    return ConditionalDebugLogger(logger, component)
