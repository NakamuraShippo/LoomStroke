"""Prompt composition logic for PromptEditor plugin.

This module handles the combination of snippets into complete prompts
using templates and weights.
"""

from __future__ import annotations

import logging
import uuid

from loomstroke.core.event_bus_extensions import ExtendedEventBus
from loomstroke.plugins.common.constants import DEFAULT_SEPARATOR, EventTypes
from loomstroke.plugins.common.events import PluginEvent
from loomstroke.plugins.common.models import (
    ComposedPrompt,
    PromptTemplate,
    Snippet,
    generate_prompt_id,
)

from .bus_utils import request_with_timeout

logger = logging.getLogger(__name__)


class PromptComposer:
    """Manages prompt composition from snippets.

    This class handles fetching snippets from TextEditor and
    combining them into complete prompts.
    """

    def __init__(self, event_bus: ExtendedEventBus) -> None:
        """Initialize the prompt composer.

        Args:
            event_bus: The extended event bus for plugin communication
        """
        self.event_bus = event_bus

    async def fetch_snippets(self, snippet_ids: list[str]) -> list[Snippet]:
        """Fetch snippets from TextEditor plugin.

        Args:
            snippet_ids: List of snippet IDs to fetch

        Returns:
            List of Snippet objects
        """
        snippets = []

        for snippet_id in snippet_ids:
            try:
                # Create request event
                event = PluginEvent.create_request(
                    event_type=EventTypes.TEXTEDITOR_SNIPPET_GET,
                    source_plugin="prompt_editor",
                    payload={"snippet_id": snippet_id},
                )

                # Request snippet from TextEditor
                response = await request_with_timeout(self.event_bus, event, timeout=5.0)

                if response.get("success") and "snippet" in response:
                    snippet_data = response["snippet"]
                    snippet = Snippet.from_dict(snippet_data)
                    snippets.append(snippet)
                else:
                    logger.warning(
                        f"Failed to fetch snippet {snippet_id}: "
                        f"{response.get('error', 'Unknown error')}"
                    )

            except (TimeoutError, AttributeError, KeyError, ValueError) as e:
                logger.error(f"Error fetching snippet {snippet_id}: {e}")
                continue

        return snippets

    async def compose_prompt(
        self,
        snippet_ids: list[str],
        template: PromptTemplate | None = None,
        weights: dict[str, float] | None = None,
        separator: str = DEFAULT_SEPARATOR,
    ) -> ComposedPrompt:
        """Compose a prompt from snippets.

        Args:
            snippet_ids: List of snippet IDs to combine
            template: Optional template to use for composition
            weights: Optional weight values for snippets
            separator: Separator string for simple joining

        Returns:
            ComposedPrompt object with positive and negative prompts
        """
        # Fetch snippets from TextEditor
        snippets = await self.fetch_snippets(snippet_ids)

        if not snippets:
            logger.warning("No snippets fetched for composition")
            return ComposedPrompt(
                id=str(uuid.uuid4()),
                positive_prompt="",
                negative_prompt="",
                snippet_ids=snippet_ids,
                template_id=template.id if template else None,
                weights=weights or {},
                metadata={},
            )

        # Apply template or simple join
        if template:
            positive, negative = template.apply(snippets, weights)
        else:
            positive, negative = self._simple_compose(snippets, weights, separator)

        # Generate ID based on content
        prompt_id = generate_prompt_id(positive + negative)

        return ComposedPrompt(
            id=prompt_id,
            positive_prompt=positive,
            negative_prompt=negative,
            snippet_ids=snippet_ids,
            template_id=template.id if template else None,
            weights=weights or {},
            metadata={"snippet_count": len(snippets), "separator": separator},
        )

    def _simple_compose(
        self, snippets: list[Snippet], weights: dict[str, float] | None, separator: str
    ) -> tuple[str, str]:
        """Simple composition by joining snippets.

        Args:
            snippets: List of snippets to join
            weights: Optional weights for emphasis
            separator: Separator string

        Returns:
            Tuple of (positive_prompt, negative_prompt)
        """
        positive_parts = []
        negative_parts = []

        for snippet in snippets:
            content = snippet.content

            # Apply weight if provided
            if weights and snippet.id in weights:
                weight = weights[snippet.id]
                if weight > 1.0:
                    # Emphasize with parentheses
                    emphasis_level = int(weight)
                    content = "(" * emphasis_level + content + ")" * emphasis_level
                elif weight < 1.0:
                    # De-emphasize with brackets
                    content = f"[{content}]"

            # Separate negative prompts (those in "negative" category)
            if snippet.category == "negative":
                negative_parts.append(content)
            else:
                positive_parts.append(content)

        positive = separator.join(positive_parts)
        negative = separator.join(negative_parts)

        return positive, negative

    async def apply_template(
        self,
        snippets: list[Snippet],
        template: PromptTemplate,
        weights: dict[str, float] | None = None,
    ) -> tuple[str, str]:
        """Apply a template to create prompts.

        This is a wrapper around template.apply() for consistency.

        Args:
            snippets: List of snippets to use
            template: Template to apply
            weights: Optional weight values

        Returns:
            Tuple of (positive_prompt, negative_prompt)
        """
        return template.apply(snippets, weights)
