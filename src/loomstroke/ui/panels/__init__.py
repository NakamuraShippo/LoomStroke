"""
UI panels for LoomStroke.

This package provides modular UI panels following Master.md specifications.
Each panel is under 400 lines for maintainability.
"""

from .connection_panel import ConnectionPanel
from .preview_panel import PreviewPanel
from .settings_panel import SettingsPanel
from .tool_panel import ToolPanel

__all__ = [
    "ConnectionPanel",
    "PreviewPanel",
    "SettingsPanel",
    "ToolPanel",
]

__version__ = "1.0.0"
