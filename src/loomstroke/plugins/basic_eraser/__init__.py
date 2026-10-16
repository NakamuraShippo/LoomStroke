"""
Basic Eraser plugin for LoomStroke.

Implements a pressure-sensitive eraser tool as a plugin.
"""

from .eraser_tool import BasicEraserPlugin, create_plugin

__all__ = ["BasicEraserPlugin", "create_plugin"]
__version__ = "1.0.0"
