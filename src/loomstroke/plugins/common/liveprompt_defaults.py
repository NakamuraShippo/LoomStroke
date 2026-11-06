"""Read defaults for new LivePrompt compositions without changing saved state."""

from __future__ import annotations

from typing import Any

from loomstroke.platform.settings_manager import get_settings_manager

DEFAULT_SEPARATOR_KEY = "ui/liveprompt/default_separator"
DEFAULT_SEPARATOR = r"\n"


def default_separator(settings: Any | None = None) -> str:
    settings = settings if settings is not None else get_settings_manager()
    value = settings.get(DEFAULT_SEPARATOR_KEY, DEFAULT_SEPARATOR)
    # Preserve whitespace: a single space is a useful separator.
    return value if isinstance(value, str) and value else DEFAULT_SEPARATOR
