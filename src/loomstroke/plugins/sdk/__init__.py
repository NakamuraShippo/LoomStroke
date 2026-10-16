"""
LoomStroke Plugin SDK.

This package provides the base classes and protocols for creating LoomStroke plugins.
All plugins must implement the appropriate protocol from this SDK.
"""

from .base import (
    AIServicePlugin,
    JobSpec,
    PluginBase,
    PluginLifecycle,
    ProgressEvent,
    ServiceDescriptor,
)
from .errors import (
    PluginError,
    PluginInitError,
    PluginLifecycleError,
    PluginRuntimeError,
)
from .registry import PluginRegistry
from .tool import (
    PropertySpec,
    PropertyType,
    ToolActionGroup,
    ToolActionSpec,
    ToolDescriptor,
    ToolMetadata,
    ToolPlugin,
    ToolPreset,
)

__all__ = [
    # AI service plugin
    "AIServicePlugin",
    "JobSpec",
    # Base classes
    "PluginBase",
    # Errors
    "PluginError",
    "PluginInitError",
    "PluginLifecycle",
    "PluginLifecycleError",
    # Registry
    "PluginRegistry",
    "PluginRuntimeError",
    "ProgressEvent",
    "PropertySpec",
    "PropertyType",
    "ServiceDescriptor",
    "ToolActionGroup",
    "ToolActionSpec",
    "ToolDescriptor",
    "ToolMetadata",
    # Tool plugin
    "ToolPlugin",
    "ToolPreset",
]

__version__ = "1.0.0"
