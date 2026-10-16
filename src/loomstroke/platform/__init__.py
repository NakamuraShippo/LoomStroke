"""
Platform-specific services for LoomStroke.

This package provides platform integration services like
async management, settings, and logging.
"""

from .async_manager import AsyncManager, get_async_manager
from .async_manager_enhanced import EnhancedAsyncManager, get_enhanced_async_manager
from .settings_manager import SettingsManager, get_settings_manager
from .shortcut_manager import ShortcutAction, ShortcutManager, ShortcutServiceAdapter

__all__ = [
    "AsyncManager",
    "EnhancedAsyncManager",
    "SettingsManager",
    "ShortcutAction",
    "ShortcutManager",
    "ShortcutServiceAdapter",
    "get_async_manager",
    "get_enhanced_async_manager",
    "get_settings_manager",
]

__version__ = "1.0.0"
