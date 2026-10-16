"""Configuration helpers for the LLM gateway plugin."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from loomstroke.config.secret_provider import SecretNotConfigured, SecretProvider

# Public API of this module. ``SecretNotConfigured`` is re-exported
# so ``tool.py`` (and other gateway code paths) can translate the
# missing-key state into provider-specific errors without importing
# from the SDK secret-provider module directly.
__all__ = [
    "LLMConfig",
    "SecretNotConfigured",
    "available_api_key_providers",
    "known_models",
    "load",
    "resolve_model",
    "update",
    "use_api_key",
]

_NAMESPACE = "llm"
# Default model per provider. Pinned to currently-served IDs as of
# 2026-04 — older Claude 3.x ``-latest`` aliases now return 404 from
# the Messages API, so a stale default would break Anthropic out of
# the box. Users override these via the LLM panel / settings.
_DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
}
# Selectable model list per provider, surfaced in the LLM panel's
# Model dropdown. The user-configured model (``cfg.models[provider]``)
# is treated as authoritative — if the user pasted a model name that
# is not in this curated list, the runtime preserves it. The list
# only seeds the dropdown so the common case (pick from current
# offerings) does not require typing free-form.
#
# Ordered roughly best/most-recent first so the natural top pick is
# also the recommended default. Maintenance: bump these when vendors
# retire models or ship new ones — stale entries cause user-visible
# 404s (see Claude 3.x ``-latest`` retirement, 2026-04).
_KNOWN_MODELS: dict[str, list[str]] = {
    "openai": [
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o",
        "gpt-4o-mini",
        "o4-mini",
        "o3-mini",
    ],
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
}
_PROVIDER_SAFE_RE = re.compile(r"[^a-z0-9._-]+")
_SECURE_API_KEY_SERVICE = "llm_gateway"

logger = logging.getLogger(__name__)


# Phase 8a / 3 of 4: LLM gateway routes through the same
# ``SecretProvider`` singleton as NanoBanana so plugin code never
# touches keyring directly.
_SECRET_PROVIDER: SecretProvider | None = None


def _secrets() -> SecretProvider:
    """Lazy module-level SecretProvider singleton.

    A single instance is built on first credential access and reused
    for the remainder of the process's life. The provider itself
    holds NO secret values on its attributes — every ``use`` /
    ``has`` call goes through ``SecurityConfig`` which delegates to
    the OS keyring.
    """
    global _SECRET_PROVIDER  # noqa: PLW0603 - module-level lazy singleton
    if _SECRET_PROVIDER is None:
        _SECRET_PROVIDER = SecretProvider()
    return _SECRET_PROVIDER


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    normalized = _PROVIDER_SAFE_RE.sub("_", normalized)
    return normalized or "provider"


@contextmanager
def use_api_key(provider: str) -> Iterator[str]:
    """Yield the API key for *provider* for the duration of a network call.

    This is the read-on-demand path that ``tool.py`` uses to satisfy
    the Phase 8a "no app-side key handling" policy: the key is fetched
    from keyring at the entry of the ``with`` block, handed to the
    adapter, and the local binding falls out of scope on exit. This
    function never returns a bare value — the secret is only reachable
    inside the ``with`` block.

    Raises ``SecretNotConfigured`` when no credential is stored for
    *provider*. Gateway implementations translate that to a UI-visible
    "configure your key in Preferences" message.
    """
    normalized = _normalize_provider(provider)
    with _secrets().use(_SECURE_API_KEY_SERVICE, username=normalized) as key:
        yield key


def available_api_key_providers() -> set[str]:
    """Return the set of provider IDs that have a credential stored.

    Bool-only existence check via SecurityConfig.has_credential per
    candidate provider — no secret values pulled into Python memory.
    """
    providers: set[str] = set()
    try:
        manager = _secrets()._manager()  # noqa: SLF001 - bool/list metadata only
    except Exception:
        return providers
    list_credentials = getattr(manager, "list_credentials", None)
    if callable(list_credentials):
        try:
            providers.update(
                {
                    name.lower()
                    for name in list_credentials(_SECURE_API_KEY_SERVICE)
                    if isinstance(name, str) and name
                }
            )
        except Exception as exc:
            logger.debug("Failed to list secure API key providers: %s", exc)
    return providers


@dataclass(slots=True)
class LLMConfig:
    """Resolved configuration values for the plugin."""

    provider: str
    models: dict[str, str]
    timeout: float
    max_concurrency: int
    verify_ssl: bool
    max_image_mb: int
    layer_snapshot_fmt: str
    layer_queue_size: int
    system_prompts: dict[str, str]


def _read(settings: Any, key: str, default: Any) -> Any:
    # Try 'get' first (SettingsService interface)
    getter = getattr(settings, "get", None)
    if not callable(getter):
        # Fallback to 'value' (QSettings interface)
        getter = getattr(settings, "value", None)

    if callable(getter):
        try:
            value = getter(key, default)
        except Exception:
            return default

        if value is None:
            return default

        # If expecting a dict but got a string, try to parse as JSON
        if isinstance(default, dict) and isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return default

        return value
    return default


def load(settings: Any) -> LLMConfig:
    """Load configuration values from QSettings-like storage."""

    def _ns(key: str) -> str:
        return f"{_NAMESPACE}/{key}"

    provider = str(_read(settings, _ns("provider"), "openai")).lower()
    models_raw = _read(settings, _ns("model"), {})
    models: dict[str, str] = {**_DEFAULT_MODELS}
    if isinstance(models_raw, Mapping):
        models.update({str(k): str(v) for k, v in models_raw.items()})

    timeout = float(_read(settings, _ns("request_timeout_sec"), 60))
    max_concurrency = int(_read(settings, _ns("max_concurrency"), 2))
    verify_ssl = bool(int(_read(settings, _ns("verify_ssl"), 1)))
    max_image_mb = int(_read(settings, _ns("max_image_mb"), 4))
    layer_snapshot_fmt = str(_read(settings, _ns("layer_snapshot_fmt"), "png"))
    layer_queue_size = int(_read(settings, _ns("layer_request_queue_size"), 4))
    system_prompts_raw = _read(settings, _ns("system_prompt"), {})
    system_prompts: dict[str, str] = {}
    if isinstance(system_prompts_raw, Mapping):
        system_prompts.update({str(k): str(v) for k, v in system_prompts_raw.items()})

    return LLMConfig(
        provider=provider,
        models=models,
        timeout=timeout,
        max_concurrency=max_concurrency,
        verify_ssl=verify_ssl,
        max_image_mb=max_image_mb,
        layer_snapshot_fmt=layer_snapshot_fmt,
        layer_queue_size=layer_queue_size,
        system_prompts=system_prompts,
    )


def update(settings: Any, config: LLMConfig) -> None:
    """Persist configuration changes back to settings storage."""

    # Try 'set' first (SettingsService interface)
    setter = getattr(settings, "set", None)
    if not callable(setter):
        # Fallback to 'setValue' (QSettings interface)
        setter = getattr(settings, "setValue", None)
    if not callable(setter):
        return

    def _ns(key: str) -> str:
        return f"{_NAMESPACE}/{key}"

    try:
        setter(_ns("provider"), config.provider)
        setter(_ns("model"), config.models)
        setter(_ns("request_timeout_sec"), int(config.timeout))
        setter(_ns("max_concurrency"), config.max_concurrency)
        setter(_ns("verify_ssl"), int(config.verify_ssl))
        setter(_ns("max_image_mb"), config.max_image_mb)
        setter(_ns("layer_snapshot_fmt"), config.layer_snapshot_fmt)
        setter(_ns("layer_request_queue_size"), config.layer_queue_size)
        setter(_ns("system_prompt"), config.system_prompts)
    except Exception:
        # Settings writes are best-effort; swallow exceptions to avoid crashing plugins.
        return


def resolve_model(config: LLMConfig, provider: str, override: str | None) -> str:
    """Return the model name for a provider, honoring overrides."""

    if override:
        return override
    provider_key = provider.lower()
    return config.models.get(provider_key, _DEFAULT_MODELS.get(provider_key, provider_key))


def known_models(provider: str) -> list[str]:
    """Return the curated list of selectable models for *provider*.

    Used by the LLM panel's Model dropdown. Returns an empty list for
    unknown providers — the caller is expected to fall back to a
    single-entry list (typically the provider's currently-configured
    or default model) so the UI never has an empty combo.
    """
    return list(_KNOWN_MODELS.get(provider.lower(), []))
