"""User filter persistence and matching for ComfyUI node tabs."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class UserFilterConfig:
    """User defined filtering rules persisted via SettingsManager."""

    filter_id: str
    display_name: str
    name_keywords: tuple[str, ...] = ()
    parameter_keywords: tuple[str, ...] = ()
    value_types: tuple[str, ...] = ()


class UserFilterManager:
    """Owns user filter serialization and node metadata matching."""

    def __init__(self, settings: Any, settings_key: str) -> None:
        self._settings = settings
        self._settings_key = settings_key

    def load(self) -> list[UserFilterConfig]:
        raw = self._settings.get(self._settings_key, "")
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            logger.warning("Failed to parse user filter settings")
            return []
        filters: list[UserFilterConfig] = []
        for entry in data if isinstance(data, list) else []:
            if not isinstance(entry, dict):
                continue
            filter_id = entry.get("filter_id") or entry.get("id") or str(uuid4())
            filters.append(
                UserFilterConfig(
                    filter_id=str(filter_id),
                    display_name=str(entry.get("display_name", "Custom")),
                    name_keywords=self.normalize_keywords(entry.get("name_keywords")),
                    parameter_keywords=self.normalize_keywords(entry.get("parameter_keywords")),
                    value_types=self.normalize_keywords(entry.get("value_types")),
                )
            )
        return filters

    def save(self, filters: Iterable[UserFilterConfig]) -> None:
        payload = [
            {
                "filter_id": cfg.filter_id,
                "display_name": cfg.display_name,
                "name_keywords": list(cfg.name_keywords),
                "parameter_keywords": list(cfg.parameter_keywords),
                "value_types": list(cfg.value_types),
            }
            for cfg in filters
        ]
        self._settings.set(
            self._settings_key,
            json.dumps(payload, ensure_ascii=False),
        )

    def matches(self, meta: Any, config: UserFilterConfig) -> bool:
        if config.name_keywords and not self.keyword_match(
            [getattr(meta, "display_name", ""), getattr(meta, "node_type", "")],
            config.name_keywords,
        ):
            return False
        if config.parameter_keywords and not self.keyword_match(
            getattr(meta, "param_names", ()),
            config.parameter_keywords,
        ):
            return False
        if config.value_types:
            return set(config.value_types).issubset(set(getattr(meta, "value_types", ())))
        return True

    @staticmethod
    def keyword_match(targets: Iterable[str], keywords: Iterable[str]) -> bool:
        lowered = [str(value).lower() for value in targets if value]
        for keyword in keywords:
            kw = keyword.lower()
            if any(kw in target for target in lowered):
                return True
        return False

    @staticmethod
    def normalize_keywords(raw_value: Any) -> tuple[str, ...]:
        if raw_value is None:
            return ()
        items = [raw_value] if isinstance(raw_value, str) else list(raw_value)
        tokens: list[str] = []
        for item in items:
            for token in str(item).replace("\n", ",").split(","):
                stripped = token.strip().lower()
                if stripped:
                    tokens.append(stripped)
        seen: list[str] = []
        for token in tokens:
            if token not in seen:
                seen.append(token)
        return tuple(seen)


__all__ = ["UserFilterConfig", "UserFilterManager"]
