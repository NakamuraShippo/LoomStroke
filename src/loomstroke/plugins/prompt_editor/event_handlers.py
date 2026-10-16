"""Event handlers for PromptEditor plugin.

This module handles event-based communication for prompt composition
and ComfyUI integration.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from loomstroke.core.event_bus import EventToken
from loomstroke.core.event_bus_extensions import ExtendedEventBus
from loomstroke.plugins.common.constants import EventTypes
from loomstroke.plugins.common.events import PluginEvent
from loomstroke.plugins.common.models import ComposedPrompt, PromptTemplate, Snippet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDict definitions for event payloads
# ---------------------------------------------------------------------------


class GetSnippetPayload(TypedDict):
    """Payload for texteditor.snippet.get request."""

    snippet_id: str


class SearchSnippetsPayload(TypedDict, total=False):
    """Payload for texteditor.snippet.search request."""

    query: str
    filters: dict[str, Any]


class ComposePromptPayload(TypedDict, total=False):
    """Payload for prompteditor.compose.request."""

    snippet_ids: list[str]
    template: dict[str, Any] | None
    weights: dict[str, float] | None
    separator: str


class SendToComfyUIPayload(TypedDict, total=False):
    """Payload for prompteditor.prompt.sent_to_comfyui notification."""

    prompt_data: dict[str, Any]
    comfyui_params: dict[str, Any]


class SnippetResponse(TypedDict, total=False):
    """Response from snippet request handlers."""

    success: bool
    error: str
    snippet: dict[str, Any]
    snippets: list[dict[str, Any]]
    categories: list[str]
    tags: list[str]


class ComposeResponse(TypedDict, total=False):
    """Response from compose prompt handler."""

    success: bool
    error: str
    composed_prompt: dict[str, Any]


class SnippetEventHandler:
    """Handles snippet request/notification events (migrated from TextEditor).

    Registers EventBus request handlers so that other plugins can fetch
    snippets, categories, and tags via ``texteditor.snippet.*`` events.
    """

    def __init__(
        self,
        event_bus: ExtendedEventBus,
        repository: Any,  # SnippetRepository
    ) -> None:
        self.event_bus = event_bus
        self.repository = repository
        self._request_tokens: list[tuple[str, EventToken]] = []
        self._register_handlers()

    def _register_handlers(self) -> None:
        for event_type, handler in (
            (EventTypes.TEXTEDITOR_SNIPPET_GET, self._handle_get_snippet),
            (EventTypes.TEXTEDITOR_SNIPPET_SEARCH, self._handle_search_snippets),
            (EventTypes.TEXTEDITOR_CATEGORIES_LIST, self._handle_list_categories),
            (EventTypes.TEXTEDITOR_TAGS_LIST, self._handle_list_tags),
        ):
            token = self.event_bus.register_request_handler(event_type, handler)
            self._request_tokens.append((event_type, token))
        logger.info("Snippet event handlers registered (via PromptEditor)")

    async def _handle_get_snippet(self, event: PluginEvent) -> SnippetResponse:
        try:
            payload: GetSnippetPayload = event.payload  # type: ignore[assignment]  # TypedDict runtime cast from event.payload
            snippet_id = payload.get("snippet_id")
            if not snippet_id:
                return {"success": False, "error": "snippet_id is required"}
            snippet = await self.repository.get_by_id(snippet_id)
            if not snippet:
                return {"success": False, "error": f"Snippet {snippet_id} not found"}
            return {"success": True, "snippet": snippet.to_dict()}
        except Exception as e:
            logger.error("Error getting snippet: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    async def _handle_search_snippets(self, event: PluginEvent) -> SnippetResponse:
        try:
            payload: SearchSnippetsPayload = event.payload  # type: ignore[assignment]  # TypedDict runtime cast from event.payload
            query = payload.get("query", "")
            filters = payload.get("filters", {})
            snippets = await self.repository.search_snippets(query, filters)
            return {"success": True, "snippets": [s.to_dict() for s in snippets]}
        except Exception as e:
            logger.error("Error searching snippets: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    async def _handle_list_categories(self, event: PluginEvent) -> SnippetResponse:
        try:
            categories = await self.repository.list_categories()
            return {"success": True, "categories": categories}
        except Exception as e:
            logger.error("Error listing categories: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    async def _handle_list_tags(self, event: PluginEvent) -> SnippetResponse:
        try:
            tags = await self.repository.list_tags()
            return {"success": True, "tags": tags}
        except Exception as e:
            logger.error("Error listing tags: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def publish_snippet_created(self, snippet: Snippet) -> None:
        event = PluginEvent.create_notification(
            event_type=EventTypes.TEXTEDITOR_SNIPPET_CREATED,
            source_plugin="prompt_editor",
            payload={"snippet": snippet.to_dict()},
        )
        self.event_bus.emit(event.event_type, event)

    def publish_snippet_updated(self, snippet: Snippet) -> None:
        event = PluginEvent.create_notification(
            event_type=EventTypes.TEXTEDITOR_SNIPPET_UPDATED,
            source_plugin="prompt_editor",
            payload={"snippet": snippet.to_dict()},
        )
        self.event_bus.emit(event.event_type, event)

    def publish_snippet_deleted(self, snippet_id: str) -> None:
        event = PluginEvent.create_notification(
            event_type=EventTypes.TEXTEDITOR_SNIPPET_DELETED,
            source_plugin="prompt_editor",
            payload={"snippet_id": snippet_id},
        )
        self.event_bus.emit(event.event_type, event)

    def cleanup(self) -> None:
        for event_type, token in self._request_tokens:
            try:
                self.event_bus.unregister_request_handler(event_type, token)
            except Exception:
                logger.debug(
                    "Failed to unregister snippet handler for %s",
                    event_type,
                    exc_info=True,
                )
        self._request_tokens.clear()
        logger.info("Snippet event handlers cleaned up")


class PromptEditorEventHandler:
    """Handles event-based requests for PromptEditor plugin.

    This class manages prompt composition requests and ComfyUI
    communication via the event bus.
    """

    def __init__(
        self,
        event_bus: ExtendedEventBus,
        composer: Any,  # Will be PromptComposer
    ) -> None:
        """Initialize the event handler.

        Args:
            event_bus: The extended event bus for communication
            composer: The prompt composer for creating prompts
        """
        self.event_bus = event_bus
        self.composer = composer
        self._request_tokens: list[tuple[str, EventToken]] = []
        self._subscription_tokens: list[EventToken] = []
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register all event handlers."""
        # Register request handlers
        token = self.event_bus.register_request_handler(
            EventTypes.PROMPTEDITOR_COMPOSE_REQUEST, self._handle_compose_prompt
        )
        self._request_tokens.append((EventTypes.PROMPTEDITOR_COMPOSE_REQUEST, token))

        # Register notification handlers
        token = self.event_bus.on(
            EventTypes.PROMPTEDITOR_PROMPT_SENT_TO_COMFYUI, self._handle_send_to_comfyui
        )
        self._subscription_tokens.append(token)

        logger.info("PromptEditor event handlers registered")

    async def _handle_compose_prompt(self, event: PluginEvent) -> ComposeResponse:
        """Handle request to compose a prompt.

        Args:
            event: Event containing snippet_ids, template, weights, etc.

        Returns:
            Dictionary with composed prompt or error
        """
        try:
            payload: ComposePromptPayload = event.payload  # type: ignore[assignment]  # TypedDict runtime cast from event.payload
            snippet_ids = payload.get("snippet_ids", [])
            if not snippet_ids:
                return {"success": False, "error": "snippet_ids is required"}

            # Get optional parameters
            template_data = payload.get("template")
            template = None
            if template_data:
                template = PromptTemplate(**template_data)

            weights = payload.get("weights")
            separator = payload.get("separator", ", ")

            # Compose prompt
            composed = await self.composer.compose_prompt(
                snippet_ids=snippet_ids, template=template, weights=weights, separator=separator
            )

            # Publish composition event
            await self.publish_prompt_composed(composed)

            return {"success": True, "composed_prompt": composed.to_dict()}

        except Exception as e:
            logger.error("Error composing prompt: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    async def _handle_send_to_comfyui(self, event: PluginEvent) -> None:
        """Handle sending prompt to ComfyUI.

        Args:
            event: Event containing prompt data and ComfyUI parameters
        """
        try:
            payload: SendToComfyUIPayload = event.payload  # type: ignore[assignment]  # TypedDict runtime cast from event.payload
            prompt_data = payload.get("prompt_data")
            comfyui_params = payload.get("comfyui_params", {})

            if not prompt_data:
                logger.error("No prompt data provided for ComfyUI")
                return

            # Create ComfyUI generation event
            comfyui_event = PluginEvent.create_notification(
                event_type=EventTypes.COMFYUI_GENERATE_IMAGE,
                source_plugin="prompt_editor",
                payload={
                    "positive_prompt": prompt_data.get("positive_prompt", ""),
                    "negative_prompt": prompt_data.get("negative_prompt", ""),
                    **comfyui_params,
                },
            )

            # Send to ComfyUI
            await self.event_bus.emit_async(comfyui_event.event_type, comfyui_event)

            # Publish notification that prompt was sent
            await self.publish_sent_to_comfyui(
                prompt_id=prompt_data.get("id", "unknown"),
                workflow_id=comfyui_params.get("workflow_id", "default"),
            )

            logger.info("Sent prompt to ComfyUI: %s", prompt_data.get("id", "unknown"))

        except Exception as e:
            logger.error("Error sending to ComfyUI: %s", e, exc_info=True)

    async def publish_prompt_composed(self, composed: ComposedPrompt) -> None:
        """Publish event when a prompt is composed.

        Args:
            composed: The composed prompt
        """
        event = PluginEvent.create_notification(
            event_type=EventTypes.PROMPTEDITOR_PROMPT_COMPOSED,
            source_plugin="prompt_editor",
            payload={"composed_prompt": composed.to_dict()},
        )
        await self.event_bus.emit_async(event.event_type, event)
        logger.debug("Published prompt composed event: %s", composed.id)

    async def publish_sent_to_comfyui(self, prompt_id: str, workflow_id: str) -> None:
        """Publish event when prompt is sent to ComfyUI.

        Args:
            prompt_id: ID of the sent prompt
            workflow_id: ComfyUI workflow ID
        """
        event = PluginEvent.create_notification(
            event_type=EventTypes.PROMPTEDITOR_PROMPT_SENT_TO_COMFYUI,
            source_plugin="prompt_editor",
            payload={"prompt_id": prompt_id, "workflow_id": workflow_id},
        )
        await self.event_bus.emit_async(event.event_type, event)
        logger.debug("Published sent to ComfyUI event: %s -> %s", prompt_id, workflow_id)

    async def publish_preset_saved(self, preset_id: str, preset_name: str) -> None:
        """Publish event when a preset is saved.

        Args:
            preset_id: ID of the saved preset
            preset_name: Name of the preset
        """
        event = PluginEvent.create_notification(
            event_type=EventTypes.PROMPTEDITOR_PRESET_SAVED,
            source_plugin="prompt_editor",
            payload={"preset_id": preset_id, "preset_name": preset_name},
        )
        await self.event_bus.emit_async(event.event_type, event)
        logger.debug("Published preset saved event: %s", preset_id)

    async def publish_preset_loaded(self, preset_id: str, preset_name: str) -> None:
        """Publish event when a preset is loaded.

        Args:
            preset_id: ID of the loaded preset
            preset_name: Name of the preset
        """
        event = PluginEvent.create_notification(
            event_type=EventTypes.PROMPTEDITOR_PRESET_LOADED,
            source_plugin="prompt_editor",
            payload={"preset_id": preset_id, "preset_name": preset_name},
        )
        await self.event_bus.emit_async(event.event_type, event)
        logger.debug("Published preset loaded event: %s", preset_id)

    def cleanup(self) -> None:
        """Clean up event handlers."""
        for event_type, token in self._request_tokens:
            try:
                self.event_bus.unregister_request_handler(event_type, token)
            except Exception:
                logger.debug(
                    "Failed to unregister PromptEditor handler for %s",
                    event_type,
                    exc_info=True,
                )
        for token in self._subscription_tokens:
            try:
                self.event_bus.off(token.event, token)
            except Exception:
                logger.debug(
                    "Failed to unsubscribe PromptEditor event %s",
                    token.event,
                    exc_info=True,
                )
        self._request_tokens.clear()
        self._subscription_tokens.clear()
        logger.info("PromptEditor event handlers cleaned up")
