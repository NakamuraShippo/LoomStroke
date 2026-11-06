"""Per-model request tweaks for providers whose newer models reject old parameters.

Claude 4.7+ and OpenAI reasoning models (GPT-5+, o-series) return HTTP 400
when ``temperature`` is sent, and Gemini 3+ replaces ``thinkingBudget`` with
``thinkingLevel``. Older and unknown model IDs are sent unchanged.
"""

from __future__ import annotations

import copy
import re
from typing import Any

_CLAUDE_RE = re.compile(r"^claude-(?:opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2}))?(?:-|$)")
_OPENAI_REASONING_RE = re.compile(r"^(?:gpt-(?:[5-9]|\d{2,})|o\d)(?:[.-]|$)")
_GEMINI_RE = re.compile(r"^gemini-(\d+)(?:[.-]|$)")
# Light effort: the gateway serves prompt-shaping style completions.
_EFFORT = "low"
# /v1/models lists every model the key can reach; only text chat models fit
# the LLM tab. Name rules, so a new family may need adding here.
_OPENAI_CHAT_RE = re.compile(r"^(?:gpt-|chatgpt-|o\d)")
_OPENAI_NON_CHAT = (
    "audio",
    "realtime",
    "tts",
    "transcribe",
    "image",
    "search",
    "embedding",
    "moderation",
)
_GEMINI_NON_CHAT = ("embedding", "imagen", "veo", "tts", "-image")


def is_chat_model(provider: str, model: str) -> bool:
    """Whether a listed *model* ID is a text chat model the LLM tab can use."""
    provider = provider.strip().lower()
    model = model.strip().lower()
    if provider == "openai":
        return bool(_OPENAI_CHAT_RE.match(model)) and not any(
            part in model for part in _OPENAI_NON_CHAT
        )
    if provider == "gemini":
        return bool(model) and not any(part in model for part in _GEMINI_NON_CHAT)
    return bool(model)


def _claude_rejects_sampling(model: str) -> bool:
    match = _CLAUDE_RE.match(model)
    if match is None:
        return False
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    return major >= 5 or (major == 4 and minor >= 7)


def _gemini_uses_thinking_level(model: str) -> bool:
    match = _GEMINI_RE.match(model)
    return match is not None and int(match.group(1)) >= 3


def accepts_temperature(provider: str, model: str) -> bool:
    """Whether ``temperature`` reaches *model* (it is dropped otherwise)."""
    provider = provider.strip().lower()
    model = model.strip().lower()
    if provider == "anthropic":
        return not _claude_rejects_sampling(model)
    if provider == "openai":
        return _OPENAI_REASONING_RE.match(model) is None
    return True


def tune_body(provider: str, model: str, body: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *body* adjusted for *model*; *body* itself is untouched."""
    provider = provider.strip().lower()
    model = model.strip().lower()
    if provider == "anthropic" and _claude_rejects_sampling(model):
        tuned = dict(body)
        tuned.pop("temperature", None)
        tuned["output_config"] = {**tuned.get("output_config", {}), "effort": _EFFORT}
        return tuned
    if provider == "openai" and _OPENAI_REASONING_RE.match(model):
        tuned = dict(body)
        tuned.pop("temperature", None)
        tuned["reasoning"] = {**tuned.get("reasoning", {}), "effort": _EFFORT}
        return tuned
    if provider == "gemini" and _gemini_uses_thinking_level(model):
        tuned = copy.deepcopy(body)
        generation = tuned.setdefault("generationConfig", {})
        generation["thinkingConfig"] = {"thinkingLevel": _EFFORT}
        return tuned
    return body
