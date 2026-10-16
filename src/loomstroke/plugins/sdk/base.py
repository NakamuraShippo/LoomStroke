"""
Base classes and protocols for LoomStroke plugins.

This module defines the core plugin interfaces for non-tool plugins
(AI services, filters, IO, UI panels, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class PluginType(Enum):
    """Types of plugins supported by LoomStroke."""

    TOOL = "tool"
    FILTER = "filter"
    AI_SERVICE = "ai"
    IO = "io"
    UI_PANEL = "ui"
    WORKFLOW = "workflow"
    AUTOMATION = "automation"
    KEYMAP = "keymap"


@dataclass(frozen=True)
class PluginMetadata:
    """Common metadata for all plugins."""

    id: str
    name: str
    version: str
    description: str
    author: str
    license: str = "MIT"
    edition: str = "free"
    sku: str | None = None
    requires_entitlement: bool = False
    trial_days: int | None = None
    types: list[PluginType] = None

    def __post_init__(self):
        if self.types is None:
            object.__setattr__(self, "types", [])

    @property
    def is_paid(self) -> bool:
        """Return True when this plugin should be treated as a paid plugin."""
        return self.edition.lower() != "free" or bool(self.requires_entitlement)


class PluginLifecycle(ABC):
    """
    Base lifecycle interface for all plugins.

    All plugins must implement these lifecycle methods.
    """

    @abstractmethod
    def activate(self, host_api: Any) -> None:
        """
        Called when the plugin is loaded.

        Args:
            host_api: The host API interface
        """
        ...

    @abstractmethod
    def deactivate(self) -> None:
        """Called when the plugin is unloaded."""
        ...


class PluginBase(PluginLifecycle):
    """
    Base class for plugins with common functionality.

    Provides default implementations and utility methods.
    """

    def __init__(self, metadata: PluginMetadata):
        self.metadata = metadata
        self.host_api = None
        self._enabled = False

    def activate(self, host_api: Any) -> None:
        """Store host API and perform initialization."""
        self.host_api = host_api
        self._enabled = True

    def deactivate(self) -> None:
        """Clean up resources."""
        self._enabled = False
        self.host_api = None

    @property
    def enabled(self) -> bool:
        """Check if plugin is enabled."""
        return self._enabled

    # Convenience helpers -------------------------------------------------
    def register_shortcut_action(
        self,
        action_id: str,
        *,
        label: str,
        default: str | None = None,
        category: str = "General",
        description: str | None = None,
    ) -> str | None:
        """Proxy to HostAPI.register_shortcut_action when available."""

        if not self.host_api:
            raise RuntimeError("Host API not attached to plugin")
        register = getattr(self.host_api, "register_shortcut_action", None)
        if not callable(register):
            raise RuntimeError("Host API does not expose shortcut registration")
        return register(
            action_id,
            label=label,
            default=default,
            category=category,
            description=description,
        )


# ---- AI Service Plugin ----


@dataclass(frozen=True)
class ServiceDescriptor:
    """Descriptor for an AI service."""

    name: str
    capabilities: set[str]  # e.g., {"text2img", "img2img", "inpaint", "upscale"}
    max_concurrency: int = 1
    max_resolution: tuple[int, int] = (2048, 2048)
    supports_streaming: bool = True
    requires_api_key: bool = False


@dataclass(frozen=True)
class JobSpec:
    """Specification for an AI job."""

    op: str  # Operation type (e.g., "text2img", "img2img")
    params: Mapping[str, Any]  # Operation parameters
    priority: int = 0  # Job priority (higher = more important)
    timeout: float | None = None  # Timeout in seconds


@dataclass(frozen=True)
class ProgressEvent:
    """Progress event from AI service."""

    job_id: str
    step: int
    total: int
    message: str | None = None
    preview_png: bytes | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class JobResult:
    """Result from an AI job (backward-compatible schema)."""

    job_id: str
    success: bool = True
    output_type: str = "image"
    data: Any | None = None  # Preferred field for structured payloads
    output: bytes | None = None  # Legacy binary payload
    error: str | None = None
    metadata: dict[str, Any] | None = None


@runtime_checkable
class AIServicePlugin(Protocol):
    """
    Protocol for AI service plugins.

    Handles connection to AI services (local or remote) for
    generation, processing, and enhancement operations.
    """

    @abstractmethod
    def descriptor(self) -> ServiceDescriptor:
        """Get service descriptor."""
        ...

    @abstractmethod
    async def health(self) -> bool:
        """Check if service is healthy and reachable."""
        ...

    @abstractmethod
    async def submit(self, spec: JobSpec) -> str:
        """
        Submit a job to the service.

        Args:
            spec: Job specification

        Returns:
            Job ID for tracking
        """
        ...

    @abstractmethod
    async def stream(self, job_id: str) -> AsyncIterator[ProgressEvent]:
        """
        Stream progress events for a job.

        Args:
            job_id: Job ID to stream

        Yields:
            Progress events
        """
        ...

    @abstractmethod
    async def cancel(self, job_id: str) -> bool:
        """
        Cancel a running job.

        Args:
            job_id: Job ID to cancel

        Returns:
            True if cancelled successfully
        """
        ...

    @abstractmethod
    async def result(self, job_id: str) -> JobResult:
        """
        Get the final result of a job.

        Args:
            job_id: Job ID

        Returns:
            Job result
        """
        ...


# ---- Filter Plugin ----


@runtime_checkable
class FilterPlugin(Protocol):
    """
    Protocol for filter plugins.

    Filters process layers or selections to apply effects.
    They generate Commands like other plugins.
    """

    def name(self) -> str:
        """Get filter name."""
        ...

    def category(self) -> str:
        """Get filter category (e.g., "blur", "color", "artistic")."""
        ...

    def apply(
        self, layer_id: str, params: dict[str, Any], selection: Any = None
    ) -> Any:  # Returns Command
        """
        Apply filter and return a Command.

        Args:
            layer_id: Target layer ID
            params: Filter parameters
            selection: Optional selection to constrain filter

        Returns:
            Command to apply the filter
        """
        ...

    def create_options_widget(self) -> Any:
        """Create options widget for filter parameters."""
        ...


# ---- IO Plugin ----


@runtime_checkable
class IOPlugin(Protocol):
    """
    Protocol for import/export plugins.

    Handles reading and writing various file formats.
    """

    def supported_formats(self) -> list[str]:
        """Get list of supported file extensions (e.g., ['.psd', '.kra'])."""
        ...

    async def can_read(self, path: str) -> bool:
        """Check if the plugin can read a file."""
        ...

    async def can_write(self, format: str) -> bool:
        """Check if the plugin can write a format."""
        ...

    async def read(
        self,
        path: str,
        options: dict[str, Any] | None = None,
    ) -> Any:
        """
        Read a file and return document data.

        Args:
            path: File path to read
            options: Read options

        Returns:
            Document data structure
        """
        ...

    async def write(
        self,
        path: str,
        document: Any,
        options: dict[str, Any] | None = None,
    ) -> bool:
        """
        Write document to file.

        Args:
            path: File path to write
            document: Document data
            options: Write options

        Returns:
            True if successful
        """
        ...


# ---- UI Panel Plugin ----


@runtime_checkable
class UIPanelPlugin(Protocol):
    """
    Protocol for UI panel plugins.

    Adds custom panels to the application UI.
    """

    def panel_id(self) -> str:
        """Get unique panel ID."""
        ...

    def panel_title(self) -> str:
        """Get panel title for display."""
        ...

    def create_widget(self, parent: Any = None) -> Any:
        """
        Create the panel widget.

        Args:
            parent: Parent QWidget

        Returns:
            QWidget for the panel
        """
        ...

    def default_position(self) -> str:
        """Get default dock position (left, right, top, bottom)."""
        ...
