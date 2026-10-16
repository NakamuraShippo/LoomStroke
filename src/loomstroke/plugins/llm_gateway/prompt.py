"""Prompt composition helpers for the LLM gateway plugin."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

PromptMessage = dict[str, Any]
Message = Mapping[str, Any]


@dataclass(frozen=True)
class PlaceholderContext:
    """Mapping wrapper providing graceful fallback for missing keys."""

    values: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.values.get(key, "")


def build_messages(system: str | None, prompt: Iterable[Message]) -> list[PromptMessage]:
    """Return a list of prompt messages with optional system prefix."""

    messages: list[PromptMessage] = []
    if system:
        messages.append({"role": "system", "content": system})
    for item in prompt:
        role = str(item.get("role", "user"))
        content = item.get("content", "")
        messages.append({"role": role, "content": content})
    return messages


def build_json_schema(schema: Mapping[str, Any] | None, provider: str) -> Mapping[str, Any] | None:
    """Prepare JSON schema payloads per provider expectations."""

    if not schema:
        return None
    if provider == "openai":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema.get("name", "structured_response"),
                "schema": schema.get("schema", schema),
            },
        }
    if provider == "anthropic":
        return {
            "type": "json_schema",
            "json_schema": schema,
        }
    if provider == "gemini":
        return {
            "mime_type": "application/json",
            "schema": schema,
        }
    return schema


def inject_placeholders(template: str, context: Mapping[str, Any]) -> str:
    """Fill placeholders in prompt templates with context values."""

    if not template:
        return ""
    if not context:
        return template
    wrapper = PlaceholderContext(context)
    try:
        return template.format_map(wrapper)  # type: ignore[arg-type]  # Mapping subclass acceptable at runtime
    except Exception:
        # If formatting fails fall back to raw template.
        return template
