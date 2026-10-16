"""
Plugin-related error types.

Provides specific exceptions for plugin initialization, lifecycle, and runtime errors.
"""


class PluginError(Exception):
    """Base exception for all plugin-related errors."""

    pass


class PluginInitError(PluginError):
    """
    Raised when a plugin fails to initialize.

    This includes loading, parsing metadata, or initial validation failures.
    """

    pass


class PluginLifecycleError(PluginError):
    """
    Raised when a plugin lifecycle operation fails.

    This includes activation, deactivation, enable, or disable failures.
    """

    pass


class PluginRuntimeError(PluginError):
    """
    Raised when a plugin encounters an error during runtime.

    This includes failures during normal operation after successful activation.
    """

    pass


class PluginCompatibilityError(PluginError):
    """
    Raised when a plugin is incompatible with the host.

    This includes version mismatches or missing required features.
    """

    pass


class PluginPermissionError(PluginError):
    """
    Raised when a plugin lacks required permissions.

    This includes file system, network, or GPU access violations.
    """

    pass
