"""Credential setting constants for GPT Image 2."""

from __future__ import annotations

GPTIMAGE2_USE_OPENAI_API_KEY_SETTING = "gptimage2/use_openai_api_key"

GPTIMAGE2_API_KEY_SERVICE = "gptimage2"
GPTIMAGE2_API_KEY_USERNAME = "api"

OPENAI_API_KEY_SERVICE = "llm_gateway"
OPENAI_API_KEY_USERNAME = "openai"

__all__ = [
    "GPTIMAGE2_API_KEY_SERVICE",
    "GPTIMAGE2_API_KEY_USERNAME",
    "GPTIMAGE2_USE_OPENAI_API_KEY_SETTING",
    "OPENAI_API_KEY_SERVICE",
    "OPENAI_API_KEY_USERNAME",
]
