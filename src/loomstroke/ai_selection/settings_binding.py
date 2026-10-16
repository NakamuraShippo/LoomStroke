"""Pure settings binding helpers for the AI Select runtime.

SAM 3.1 is the only production backend; runtime configuration lives under
``sam31/*`` (see ``loomstroke.sam31.runtime_config``). The only key owned
here is a developer/testing flag that can swap in the deterministic fake
worker — it has no Preferences UI and defaults to the SAM 3.1 backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Developer/testing flag (no UI): "sam31" routes AI Select through the
# SAM 3.1 worker, "fake" uses the deterministic in-process fake client.
AI_SELECT_RUNTIME_MODE_KEY = "ai_select/runtime/mode"
AI_SELECT_RUNTIME_MODE_SAM31 = "sam31"
AI_SELECT_RUNTIME_MODE_FAKE = "fake"
AI_SELECT_RUNTIME_MODE_VALUES = (
    AI_SELECT_RUNTIME_MODE_SAM31,
    AI_SELECT_RUNTIME_MODE_FAKE,
)
AI_SELECT_FAKE_CANDIDATE_COUNT_KEY = "ai_select/runtime/fake_candidate_count"
AI_SELECT_ASSETS_MAX_CACHE_MB_KEY = "ai_select/assets/max_cache_mb"
PRIVACY_ALLOW_EXTERNAL_SERVICES_KEY = "privacy/allow_external_services"

DEFAULT_AI_SELECT_FAKE_CANDIDATE_COUNT = 2
DEFAULT_AI_SELECT_ASSETS_MAX_CACHE_MB = 2048


def ai_select_runtime_mode(
    settings: object | None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> str:
    mode = (
        _settings_str(
            settings,
            AI_SELECT_RUNTIME_MODE_KEY,
            default=AI_SELECT_RUNTIME_MODE_SAM31,
            overrides=overrides,
        )
        .strip()
        .lower()
    )
    return mode if mode in AI_SELECT_RUNTIME_MODE_VALUES else AI_SELECT_RUNTIME_MODE_SAM31


def ai_select_fake_candidate_count(
    settings: object | None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> int:
    value = _settings_int(
        settings,
        AI_SELECT_FAKE_CANDIDATE_COUNT_KEY,
        default=DEFAULT_AI_SELECT_FAKE_CANDIDATE_COUNT,
        overrides=overrides,
    )
    return max(1, min(16, value))


def _settings_get(
    settings: object | None,
    key: str,
    default: object,
    *,
    overrides: Mapping[str, Any] | None,
) -> object:
    if overrides is not None and key in overrides:
        return overrides[key]
    if settings is None:
        return default
    getter = getattr(settings, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    return default


def _settings_bool(
    settings: object | None,
    key: str,
    *,
    default: bool,
    overrides: Mapping[str, Any] | None,
) -> bool:
    if overrides is not None and key in overrides:
        value = overrides[key]
    else:
        getter = getattr(settings, "get_bool", None) if settings is not None else None
        if callable(getter):
            try:
                return bool(getter(key, default))
            except TypeError:
                return bool(getter(key))
        value = _settings_get(settings, key, default, overrides=overrides)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def _settings_int(
    settings: object | None,
    key: str,
    *,
    default: int,
    overrides: Mapping[str, Any] | None,
) -> int:
    if overrides is not None and key in overrides:
        value = overrides[key]
    else:
        getter = getattr(settings, "get_int", None) if settings is not None else None
        if callable(getter):
            try:
                value = getter(key, default)
            except (TypeError, ValueError):
                return default
        else:
            value = _settings_get(settings, key, default, overrides=overrides)

    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _settings_str(
    settings: object | None,
    key: str,
    *,
    default: str,
    overrides: Mapping[str, Any] | None,
) -> str:
    value = _settings_get(settings, key, default, overrides=overrides)
    if value is None:
        return default
    return str(value)


__all__ = [
    "AI_SELECT_ASSETS_MAX_CACHE_MB_KEY",
    "AI_SELECT_FAKE_CANDIDATE_COUNT_KEY",
    "AI_SELECT_RUNTIME_MODE_FAKE",
    "AI_SELECT_RUNTIME_MODE_KEY",
    "AI_SELECT_RUNTIME_MODE_SAM31",
    "AI_SELECT_RUNTIME_MODE_VALUES",
    "DEFAULT_AI_SELECT_ASSETS_MAX_CACHE_MB",
    "DEFAULT_AI_SELECT_FAKE_CANDIDATE_COUNT",
    "PRIVACY_ALLOW_EXTERNAL_SERVICES_KEY",
    "ai_select_fake_candidate_count",
    "ai_select_runtime_mode",
]
