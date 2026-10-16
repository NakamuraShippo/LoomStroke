"""Persistence helpers for the Prompt Editor plugin."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from loomstroke.platform.settings_manager import SettingsManager

_STORE_KEY = "prompt_editor/snippets"
_LOCAL_SOURCE = "local"


def _normalize_entry(raw: Any) -> dict[str, Any] | None:
    """Convert raw persisted value into a normalized entry."""
    if isinstance(raw, dict):
        content = str(raw.get("content", "")).strip()
        if not content:
            return None
        entry_id = str(raw.get("id") or raw.get("snippet_id") or f"local-{uuid.uuid4().hex}")
        source = str(raw.get("source") or _LOCAL_SOURCE)
        metadata = raw.get("metadata") or {}
        return {
            "id": entry_id,
            "content": content,
            "source": source,
            "metadata": metadata,
        }

    if isinstance(raw, str):
        content = raw.strip()
        if not content:
            return None
        return {
            "id": f"local-{uuid.uuid4().hex}",
            "content": content,
            "source": _LOCAL_SOURCE,
            "metadata": {},
        }

    return None


class PromptSnippetStore:
    """Manage persistence of prompt snippet metadata via SettingsManager."""

    def __init__(self, settings: SettingsManager) -> None:
        self._settings = settings

    def load(self) -> list[dict[str, Any]]:
        """Load snippets list from settings."""
        raw = self._settings.get(_STORE_KEY, [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = []

        entries: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for item in raw:
                normalized = _normalize_entry(item)
                if normalized:
                    entries.append(normalized)

        return entries

    def save(self, snippets: Sequence[Mapping[str, Any]]) -> None:
        """Persist snippets list."""
        payload: list[dict[str, Any]] = []
        for item in snippets:
            normalized = _normalize_entry(dict(item))
            if normalized:
                payload.append(normalized)
        self._settings.set(_STORE_KEY, payload)

    def extend(self, snippets: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Append snippets to the stored list and return the result."""
        existing = self.load()
        for item in snippets:
            normalized = _normalize_entry(dict(item))
            if normalized:
                existing.append(normalized)
        self._settings.set(_STORE_KEY, existing)
        return existing
