"""LivePrompt emphasis, kept per comma-separated token in ComfyUI notation.

A token entry is ``{"paren": True}`` for ``(token)`` or ``{"weight": 1.2}``
for ``(token:1.2)``; a component's ``chip_emphasis`` uses the same shape for
its whole text. Components without entries render exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

WEIGHT_MIN = 0.1
WEIGHT_MAX = 2.0
WEIGHT_STEP = 0.1
# Gateways whose prompts have no weight syntax receive the plain tokens.
WEIGHTLESS_GATEWAYS = frozenset({"nanobanana", "gptimage2"})


def _clamp_weight(value: float) -> float:
    return round(min(WEIGHT_MAX, max(WEIGHT_MIN, value)), 1)


def normalize_entry(entry: Any) -> dict[str, Any]:
    """One well-formed entry, or ``{}`` for anything else."""
    if not isinstance(entry, Mapping):
        return {}
    weight = entry.get("weight")
    if isinstance(weight, (int, float)) and not isinstance(weight, bool):
        return {"weight": _clamp_weight(float(weight))}
    return {"paren": True} if entry.get("paren") is True else {}


def normalize(raw: Any) -> dict[str, dict[str, Any]]:
    """Keep only well-formed entries; unknown shapes are dropped."""
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for token, entry in raw.items():
        if not isinstance(token, str) or not token.strip():
            continue
        normalized = normalize_entry(entry)
        if normalized:
            result[token.strip()] = normalized
    return result


def format_token(token: str, entry: Mapping[str, Any] | None) -> str:
    if not entry:
        return token
    weight = entry.get("weight")
    if weight is not None:
        return f"({token}:{weight:.1f})"
    return f"({token})" if entry.get("paren") else token


def step(entry: Mapping[str, Any] | None, delta: float) -> dict[str, Any]:
    """ComfyUI order: token -> (token) -> (token:1.0) -> +/-0.1 per step."""
    if entry and entry.get("weight") is not None:
        return {"weight": _clamp_weight(float(entry["weight"]) + delta)}
    if entry and entry.get("paren"):
        return {"weight": 1.0}
    return {"paren": True}


def split_tokens(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def apply(text: str, emphasis: Mapping[str, Mapping[str, Any]] | None) -> str:
    """Decorate matching tokens, preserving the surrounding text untouched."""
    if not emphasis or not text:
        return text
    parts = text.split(",")
    for index, part in enumerate(parts):
        token = part.strip()
        entry = emphasis.get(token) if token else None
        if entry:
            start = len(part) - len(part.lstrip())
            parts[index] = part[:start] + format_token(token, entry) + part[start + len(token) :]
    return ",".join(parts)


def decorate(text: str, component: Any) -> str:
    """Token emphasis first, then the whole-chip emphasis around the result."""
    text = apply(text, component.emphasis)
    return format_token(text, component.chip_emphasis) if text else text
