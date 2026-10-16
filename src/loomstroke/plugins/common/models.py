"""Common data models shared between plugins.

This module defines the data structures used for inter-plugin communication,
ensuring consistency across the plugin ecosystem.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Snippet:
    """Represents a text snippet managed by TextEditor.

    This is the core data structure for text snippets that can be
    combined into prompts.

    Attributes:
        id: Unique identifier for the snippet
        content: The actual text content
        category: Category for organization (e.g., "品質", "スタイル")
        tags: List of tags for filtering
        created_at: Creation timestamp
        updated_at: Last update timestamp
        version: Version number for tracking changes
        metadata: Additional arbitrary metadata
        favorite: Whether this snippet is marked as favorite
    """

    id: str
    content: str
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    favorite: bool = False
    kind: str = "text"
    list_items: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "version": self.version,
            "metadata": self.metadata,
            "favorite": self.favorite,
            "kind": self.kind,
            "list_items": list(self.list_items),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snippet:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            content=data["content"],
            category=data.get("category", "general"),
            tags=data.get("tags", []),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            version=data.get("version", 1),
            metadata=data.get("metadata", {}),
            favorite=data.get("favorite", False),
            kind=data.get("kind", "text"),
            list_items=tuple(data.get("list_items", []) or ()),
        )


@dataclass(frozen=True)
class ComposedPrompt:
    """Represents a prompt composed from multiple snippets.

    This structure is created by PromptEditor when combining
    snippets into a complete prompt.

    Attributes:
        id: Unique identifier for the composed prompt
        positive_prompt: The main prompt text
        negative_prompt: Negative prompt for exclusions
        snippet_ids: List of snippet IDs used
        template_id: Optional template ID used
        weights: Weight values for each snippet
        metadata: Additional metadata
        created_at: Composition timestamp
    """

    id: str
    positive_prompt: str
    negative_prompt: str = ""
    snippet_ids: list[str] = field(default_factory=list)
    template_id: str | None = None
    weights: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "snippet_ids": self.snippet_ids,
            "template_id": self.template_id,
            "weights": self.weights,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComposedPrompt:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            positive_prompt=data["positive_prompt"],
            negative_prompt=data.get("negative_prompt", ""),
            snippet_ids=data.get("snippet_ids", []),
            template_id=data.get("template_id"),
            weights=data.get("weights", {}),
            metadata=data.get("metadata", {}),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


@dataclass(frozen=True)
class PromptTemplate:
    """Template for structured prompt composition.

    Templates define how snippets should be combined into
    positive and negative prompts.

    Attributes:
        id: Unique template identifier
        name: Display name for the template
        positive_template: Template string for positive prompt
        negative_template: Template string for negative prompt
        placeholders: Mapping of placeholder names to snippet IDs
        description: Optional description of template usage
    """

    id: str
    name: str
    positive_template: str
    negative_template: str = ""
    placeholders: dict[str, list[str]] = field(default_factory=dict)
    description: str = ""

    def apply(
        self, snippets: list[Snippet], weights: dict[str, float] | None = None
    ) -> tuple[str, str]:
        """Apply template to create positive and negative prompts.

        Args:
            snippets: List of snippets to use
            weights: Optional weight values for snippets

        Returns:
            Tuple of (positive_prompt, negative_prompt)
        """
        # Create a mapping of snippet IDs to content
        snippet_map = {s.id: s.content for s in snippets}

        # Apply weights if provided
        if weights:
            for snippet_id, weight in weights.items():
                if snippet_id in snippet_map and weight != 1.0:
                    # Simple weight application by repeating/emphasizing
                    content = snippet_map[snippet_id]
                    if weight > 1.0:
                        # Emphasize with parentheses
                        emphasis_level = int(weight)
                        content = "(" * emphasis_level + content + ")" * emphasis_level
                    snippet_map[snippet_id] = content

        # Simple template replacement (can be enhanced)
        positive = self.positive_template
        negative = self.negative_template

        for placeholder, snippet_ids in self.placeholders.items():
            placeholder_key = f"{{{placeholder}}}"
            contents = [snippet_map.get(sid, "") for sid in snippet_ids if sid in snippet_map]
            replacement = ", ".join(filter(None, contents))

            positive = positive.replace(placeholder_key, replacement)
            negative = negative.replace(placeholder_key, replacement)

        return positive, negative


@dataclass(frozen=True)
class CompositionPreset:
    """Saved preset for prompt composition.

    Presets allow users to save and reuse specific combinations
    of snippets and settings.

    Attributes:
        id: Unique preset identifier
        name: Display name for the preset
        snippet_ids: Ordered list of snippet IDs
        template_id: Optional template to use
        weights: Weight values for snippets
        separator: Separator string for simple joining
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    id: str
    name: str
    snippet_ids: list[str]
    template_id: str | None = None
    weights: dict[str, float] = field(default_factory=dict)
    separator: str = ", "
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "snippet_ids": self.snippet_ids,
            "template_id": self.template_id,
            "weights": self.weights,
            "separator": self.separator,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositionPreset:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            snippet_ids=data["snippet_ids"],
            template_id=data.get("template_id"),
            weights=data.get("weights", {}),
            separator=data.get("separator", ", "),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


def generate_snippet_id(content: str) -> str:
    """Generate a unique ID for a snippet based on content.

    Args:
        content: The snippet content

    Returns:
        A unique ID string
    """
    hash_obj = hashlib.sha256(content.encode("utf-8"))
    return f"s_{hash_obj.hexdigest()[:12]}"


def generate_prompt_id(prompt: str) -> str:
    """Generate a unique ID for a composed prompt.

    Args:
        prompt: The composed prompt text

    Returns:
        A unique ID string
    """
    hash_obj = hashlib.sha256(prompt.encode("utf-8"))
    return f"p_{hash_obj.hexdigest()[:12]}"
