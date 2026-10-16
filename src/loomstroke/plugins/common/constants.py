"""Common constants shared between plugins.

This module defines constants that are used across multiple plugins
to ensure consistency.
"""

from __future__ import annotations

from .events import EventTypes

# Database and storage
SNIPPET_DB_NAME = "text_snippets.db"
PRESET_DIR_NAME = "prompt_presets"
TEMPLATE_DIR_NAME = "prompt_templates"

# Event bus configuration
EVENT_TIMEOUT = 5.0  # Default timeout for request-response pattern (seconds)
MAX_EVENT_RETRIES = 3  # Maximum retries for failed events

# Content limits
MAX_SNIPPET_LENGTH = 5000  # Maximum characters per snippet
MAX_PROMPT_LENGTH = 10000  # Maximum characters for composed prompt
MAX_SNIPPETS_PER_PROMPT = 50  # Maximum snippets in a single composition

# UI defaults
DEFAULT_SEPARATOR = ", "  # Default separator for joining snippets
DEFAULT_CATEGORY = "general"  # Default snippet category
DEFAULT_TEMPLATE_NAME = "simple"  # Default template for composition

# Performance settings
SEARCH_DEBOUNCE_MS = 300  # Debounce delay for search operations
CACHE_TTL_SECONDS = 300  # Cache time-to-live for snippet queries
MAX_SEARCH_RESULTS = 100  # Maximum search results to return

# File paths (relative to app data directory)
SNIPPETS_DB_PATH = "plugins/text_editor/snippets.db"
PRESETS_PATH = "plugins/prompt_editor/presets"
TEMPLATES_PATH = "plugins/prompt_editor/templates"

# Categories (default set)
DEFAULT_CATEGORIES = [
    {"name": "general", "display_name": "一般", "icon": "📝"},
    {"name": "quality", "display_name": "品質", "icon": "⭐"},
    {"name": "style", "display_name": "スタイル", "icon": "🎨"},
    {"name": "subject", "display_name": "主題", "icon": "👤"},
    {"name": "environment", "display_name": "環境", "icon": "🌍"},
    {"name": "lighting", "display_name": "照明", "icon": "💡"},
    {"name": "camera", "display_name": "カメラ", "icon": "📷"},
    {"name": "negative", "display_name": "ネガティブ", "icon": "🚫"},
]

# Tags (default set with colors)
DEFAULT_TAGS = [
    {"name": "base", "color": "#2196F3"},
    {"name": "quality", "color": "#4CAF50"},
    {"name": "style", "color": "#FF9800"},
    {"name": "character", "color": "#E91E63"},
    {"name": "background", "color": "#9C27B0"},
    {"name": "effect", "color": "#00BCD4"},
    {"name": "negative", "color": "#F44336"},
]

# Snippet kinds — canonical values for Snippet.kind
SK_TEXT = "text"
SK_LIST = "list"
SK_PROMPT = "prompt"
SK_SYSTEM_PROMPT = "system_prompt"
SK_USER_PROMPT = "user_prompt"
ALL_SNIPPET_KINDS = (SK_PROMPT, SK_TEXT, SK_LIST, SK_SYSTEM_PROMPT, SK_USER_PROMPT)

# Component kinds — used in PromptEditor compose tab
CK_STRING = "string"
CK_LIST = "list"
CK_TEXT = "text"
CK_PROMPT = "prompt"

# ComfyUI integration
COMFYUI_DEFAULT_STEPS = 20
COMFYUI_DEFAULT_CFG = 7.0
COMFYUI_DEFAULT_SAMPLER = "euler_a"
COMFYUI_DEFAULT_SCHEDULER = "normal"
COMFYUI_DEFAULT_WIDTH = 512
COMFYUI_DEFAULT_HEIGHT = 512

__all__ = [
    "ALL_SNIPPET_KINDS",
    "CACHE_TTL_SECONDS",
    "CK_LIST",
    "CK_PROMPT",
    "CK_STRING",
    "CK_TEXT",
    "COMFYUI_DEFAULT_CFG",
    "COMFYUI_DEFAULT_HEIGHT",
    "COMFYUI_DEFAULT_SAMPLER",
    "COMFYUI_DEFAULT_SCHEDULER",
    "COMFYUI_DEFAULT_STEPS",
    "COMFYUI_DEFAULT_WIDTH",
    "DEFAULT_CATEGORIES",
    "DEFAULT_CATEGORY",
    "DEFAULT_SEPARATOR",
    "DEFAULT_TAGS",
    "DEFAULT_TEMPLATE_NAME",
    "EVENT_TIMEOUT",
    "MAX_EVENT_RETRIES",
    "MAX_PROMPT_LENGTH",
    "MAX_SEARCH_RESULTS",
    "MAX_SNIPPETS_PER_PROMPT",
    "MAX_SNIPPET_LENGTH",
    "PRESETS_PATH",
    "PRESET_DIR_NAME",
    "SEARCH_DEBOUNCE_MS",
    "SK_LIST",
    "SK_PROMPT",
    "SK_SYSTEM_PROMPT",
    "SK_TEXT",
    "SK_USER_PROMPT",
    "SNIPPETS_DB_PATH",
    "SNIPPET_DB_NAME",
    "TEMPLATES_PATH",
    "TEMPLATE_DIR_NAME",
    "EventTypes",
]
