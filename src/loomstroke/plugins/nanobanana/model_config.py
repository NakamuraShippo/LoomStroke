"""Model-specific GenerateContent options for native Gemini image models."""

from __future__ import annotations

from typing import Any

from .types import NanoBananaOptions

_FLASH_THINKING_MODELS = frozenset(
    {
        "gemini-nano-banana-2.1",
        "gemini-3.1-flash-image",
        "gemini-3.1-flash-image-preview",
    }
)
_PRO_THINKING_MODELS = frozenset({"gemini-3-pro-image", "gemini-3-pro-image-preview"})
_MODERN_IMAGE_MODELS = _FLASH_THINKING_MODELS | _PRO_THINKING_MODELS


def image_config_kwargs(model: str, options: NanoBananaOptions) -> dict[str, Any]:
    """Keep 2.5 requests free of the unsupported image_size parameter."""
    config: dict[str, Any] = {"aspect_ratio": options.aspect_ratio}
    if options.image_size and model in _MODERN_IMAGE_MODELS:
        config["image_size"] = options.image_size
    return config


def thinking_config_kwargs(model: str, options: NanoBananaOptions) -> dict[str, Any]:
    """Pro supports thought summaries, but no configurable thinking level."""
    config: dict[str, Any] = {}
    if model in _FLASH_THINKING_MODELS:
        config["thinking_level"] = options.thinking_level.upper()
    if model in _MODERN_IMAGE_MODELS and options.include_thoughts:
        config["include_thoughts"] = True
    return config


def supports_google_search(model: str) -> bool:
    return model in _MODERN_IMAGE_MODELS
