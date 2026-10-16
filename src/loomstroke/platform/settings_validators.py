"""
Settings validation rules.

Per Master.md PR-D specifications, provides validation
for all configuration values.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .settings_schema import (
    AI_SELECT_ASSETS_MAX_CACHE_MB_KEY,
    AI_SELECT_FAKE_CANDIDATE_COUNT_KEY,
    AI_SELECT_RUNTIME_MODE_KEY,
    AI_SELECT_RUNTIME_MODE_VALUES,
    MCP_CONFIRMATION_POLICY_KEY,
    MCP_CONFIRMATION_POLICY_VALUES,
    MCP_LIVE_HTTP_SESSION_TOKEN_KEY,
    MCP_SKIP_HARD_CONFIRMATIONS_KEY,
    SAM31_ROOT_DIR_KEY,
    SAM31_RUNTIME_DEVICE_KEY,
    SAM31_RUNTIME_REQUEST_TIMEOUT_S_KEY,
    SAM31_SERVER_AUTO_START_KEY,
)


def _is_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "false", "0", "1", "on", "off", "yes", "no"}
    return False


def _is_path_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 4096


def _is_int_range(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


# Validation rules dictionary
VALIDATORS: dict[str, Callable[[Any], tuple[bool, str | None]]] = {
    "privacy/allow_external_services": lambda v: (
        _is_boolish(v),
        "Allow external services must be a boolean flag",
    ),
    MCP_CONFIRMATION_POLICY_KEY: lambda v: (
        isinstance(v, str) and v in MCP_CONFIRMATION_POLICY_VALUES,
        "MCP confirmation policy must be one of the supported policy values",
    ),
    MCP_SKIP_HARD_CONFIRMATIONS_KEY: lambda v: (
        _is_boolish(v),
        "Skip MCP hard confirmations must be a boolean flag",
    ),
    MCP_LIVE_HTTP_SESSION_TOKEN_KEY: lambda v: (
        isinstance(v, str) and len(v) <= 512,
        "MCP session token must be a string no longer than 512 characters",
    ),
    SAM31_ROOT_DIR_KEY: lambda v: (
        _is_path_text(v),
        "SAM 3.1 root folder must be a string",
    ),
    SAM31_RUNTIME_DEVICE_KEY: lambda v: (
        isinstance(v, str) and v in ("auto", "cpu", "cuda"),
        "SAM 3.1 device must be auto, cpu, or cuda",
    ),
    SAM31_SERVER_AUTO_START_KEY: lambda v: (
        _is_boolish(v),
        "SAM 3.1 auto start must be a boolean flag",
    ),
    SAM31_RUNTIME_REQUEST_TIMEOUT_S_KEY: lambda v: (
        _is_int_range(v, 1, 3600),
        "SAM 3.1 request timeout must be between 1 and 3600 seconds",
    ),
    AI_SELECT_RUNTIME_MODE_KEY: lambda v: (
        isinstance(v, str) and v in AI_SELECT_RUNTIME_MODE_VALUES,
        "AI Select runtime mode must be one of the supported values",
    ),
    AI_SELECT_FAKE_CANDIDATE_COUNT_KEY: lambda v: (
        _is_int_range(v, 1, 16),
        "AI Select fake candidate count must be between 1 and 16",
    ),
    AI_SELECT_ASSETS_MAX_CACHE_MB_KEY: lambda v: (
        _is_int_range(v, 64, 262144),
        "AI Select asset cache limit must be between 64 and 262144 MB",
    ),
    "render/tile_size": lambda v: (64 <= v <= 1024, "Tile size must be between 64 and 1024"),
    "connection/timeout_s": lambda v: (1 <= v <= 300, "Timeout must be between 1 and 300 seconds"),
    "connection/max_retries": lambda v: (0 <= v <= 10, "Max retries must be between 0 and 10"),
    "canvas/width": lambda v: (1 <= v <= 16384, "Width must be between 1 and 16384"),
    "canvas/height": lambda v: (1 <= v <= 16384, "Height must be between 1 and 16384"),
    "performance/max_undo_steps": lambda v: (
        1 <= v <= 1000,
        "Undo steps must be between 1 and 1000",
    ),
    "io/autosave_interval": lambda v: (
        30 <= v <= 3600,
        "Autosave interval must be between 30 and 3600 seconds",
    ),
    "io/compression_quality": lambda v: (
        1 <= v <= 100,
        "Compression quality must be between 1 and 100",
    ),
    "io/layout_directory": lambda v: (
        isinstance(v, str) and bool(v.strip()),
        "Layout directory must be a non-empty path",
    ),
    "render/cache_size": lambda v: (10 <= v <= 10000, "Cache size must be between 10 and 10000 MB"),
}


def validate_setting(key: str, value: Any) -> tuple[bool, str | None]:
    """
    Validate a setting value based on key constraints.

    Args:
        key: Setting key
        value: Value to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if key in VALIDATORS:
        try:
            is_valid, error_msg = VALIDATORS[key](value)
            if not is_valid:
                return False, error_msg
        except (TypeError, ValueError) as e:
            return False, f"Validation error: {e}"

    return True, None
