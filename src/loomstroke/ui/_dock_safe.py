"""
Safe utilities for QDockWidget operations.

This module provides safety wrappers and helpers to prevent crashes during dock widget
operations, especially during drag and drop.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)

shiboken6: Any | None
try:
    import shiboken6 as _shiboken6
except ImportError:
    shiboken6 = None
    logger.warning("shiboken6 not available - object validity checks will be limited")
else:
    shiboken6 = _shiboken6


def is_valid(obj: Any) -> bool:
    """Check if a Qt object is valid and not deleted."""
    if obj is None:
        return False

    try:
        if not obj:
            return False

        if shiboken6 is not None:
            return bool(shiboken6.isValid(obj))

        if hasattr(obj, "objectName"):
            obj.objectName()

        return True

    except Exception as error:
        logger.debug("Object validity check failed: %s", error)
        return False


def safe_disconnect(signal: Any) -> None:
    """Safely disconnect all connections from a signal."""
    if signal is None:
        return

    with suppress(Exception):
        signal.disconnect()


class WeakBox:
    """Weak reference container for Qt objects."""

    def __init__(self, obj: Any | None) -> None:
        self._ref: Callable[[], Any | None]
        if obj is None:
            self._ref = lambda: None
            return

        try:
            self._ref = weakref.ref(obj)
        except TypeError:
            logger.debug("Object %r cannot be weak-referenced; using strong reference", obj)
            self._ref = lambda: obj

    @property
    def value(self) -> Any | None:
        """Get the referenced object if it's still valid."""
        obj = self._ref()
        return obj if is_valid(obj) else None

    def __bool__(self) -> bool:
        """Return True if the referenced object is valid."""
        return self.value is not None


def safe_widget_operation(widget: Any, operation: str, *args: Any, **kwargs: Any) -> Any | None:
    """Safely perform an operation on a widget."""
    if not is_valid(widget):
        logger.debug("Widget invalid for operation %s", operation)
        return None

    try:
        method = getattr(widget, operation)
    except AttributeError as error:
        logger.warning("Widget %r missing %s: %s", widget, operation, error)
        return None

    try:
        return method(*args, **kwargs)
    except Exception as error:
        logger.warning("Failed to perform %s on widget %r: %s", operation, widget, error)
        return None


def safe_parent_check(widget: Any) -> Any | None:
    """Safely get the parent of a widget."""
    if not is_valid(widget):
        logger.debug("Widget invalid when checking parent")
        return None

    try:
        parent = widget.parent()
    except (AttributeError, RuntimeError) as error:
        logger.debug("Failed to retrieve widget parent: %s", error)
        return None

    return parent if is_valid(parent) else None
