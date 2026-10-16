"""
UI package for LoomStroke.

This package contains all user interface components including panels,
dock widgets, and safety utilities.
"""

from __future__ import annotations

from ._dock_safe import WeakBox, is_valid, safe_disconnect, safe_widget_operation
from .moving_aware_dock import (
    MovingAwareDock,
    create_moving_aware_dock,
    enable_move_aware_application,
)

__all__ = [
    "MovingAwareDock",
    "WeakBox",
    "create_moving_aware_dock",
    "enable_move_aware_application",
    "is_valid",
    "safe_disconnect",
    "safe_widget_operation",
]
