"""Common event definitions for plugin communication.

This module defines the shared event structures used for inter-plugin
communication via the event bus.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PluginEvent:
    """Base event class for plugin communication.

    All plugin events should use this structure for consistency.

    Attributes:
        event_type: The type of event (e.g., "texteditor.snippet.created")
        source_plugin: The plugin that emitted this event
        payload: Event-specific data
        correlation_id: Optional ID for request-response tracking
    """

    event_type: str
    source_plugin: str
    payload: dict[str, Any]
    correlation_id: str | None = None

    @classmethod
    def create_request(
        cls, event_type: str, source_plugin: str, payload: dict[str, Any]
    ) -> PluginEvent:
        """Create an event with a correlation ID for request-response pattern.

        Args:
            event_type: The type of event
            source_plugin: The plugin making the request
            payload: Request data

        Returns:
            PluginEvent with a unique correlation_id
        """
        return cls(
            event_type=event_type,
            source_plugin=source_plugin,
            payload=payload,
            correlation_id=str(uuid.uuid4()),
        )

    @classmethod
    def create_notification(
        cls, event_type: str, source_plugin: str, payload: dict[str, Any]
    ) -> PluginEvent:
        """Create an event without correlation ID for notifications.

        Args:
            event_type: The type of event
            source_plugin: The plugin sending notification
            payload: Notification data

        Returns:
            PluginEvent without correlation_id
        """
        return cls(
            event_type=event_type, source_plugin=source_plugin, payload=payload, correlation_id=None
        )


@dataclass(frozen=True)
class EventResponse:
    """Response structure for request-response pattern.

    Attributes:
        correlation_id: The ID from the original request
        success: Whether the request was successful
        data: Response data (if successful)
        error: Error message (if failed)
    """

    correlation_id: str
    success: bool
    data: Any | None = None
    error: str | None = None


# Event type constants
class EventTypes:
    """Constants for standard event types."""

    # TextEditor events
    TEXTEDITOR_SNIPPET_CREATED = "texteditor.snippet.created"
    TEXTEDITOR_SNIPPET_UPDATED = "texteditor.snippet.updated"
    TEXTEDITOR_SNIPPET_DELETED = "texteditor.snippet.deleted"
    TEXTEDITOR_SNIPPET_GET = "texteditor.snippet.get"
    TEXTEDITOR_SNIPPET_SEARCH = "texteditor.snippet.search"
    TEXTEDITOR_CATEGORIES_LIST = "texteditor.categories.list"
    TEXTEDITOR_TAGS_LIST = "texteditor.tags.list"

    # PromptEditor events
    PROMPTEDITOR_PROMPT_COMPOSED = "prompteditor.prompt.composed"
    PROMPTEDITOR_PROMPT_SENT_TO_COMFYUI = "prompteditor.prompt.sent_to_comfyui"
    PROMPTEDITOR_PRESET_SAVED = "prompteditor.preset.saved"
    PROMPTEDITOR_PRESET_LOADED = "prompteditor.preset.loaded"
    PROMPTEDITOR_COMPOSE_REQUEST = "prompteditor.compose.request"

    # ComfyUI events
    COMFYUI_GENERATE_IMAGE = "comfyui.generate.image"
    COMFYUI_IMAGE_READY = "comfyui.image.ready"
    COMFYUI_GENERATION_FAILED = "comfyui.generation.failed"
