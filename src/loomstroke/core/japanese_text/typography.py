"""Normalize optional Japanese typography without changing legacy text metadata."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .annotations import ANNOTATION_KEYS, normalize_annotations
from .character_fonts import has_font_overrides
from .character_geometry import geometry_runs
from .character_styles import color_runs
from .custom_rules import normalize_rules
from .frame_flow import has_frame_flow
from .metadata_values import finite_number
from .paragraph_styles import has_paragraph_styles

_DEFAULTS = {
    "enabled": False,
    "em_advance": True,
    "punctuation_spacing": "standard",
    "line_start_opening": "half",
    "line_end_closing": "half",
    "latin_spacing_em": 0.25,
    "kinsoku": "ja_standard",
    "hanging": False,
    "line_adjustment": "compress",
    "compression_limit_em": 0.5,
    "expansion_limit_em": 0.25,
    "first_line_indent_em": 0.0,
    "latin_orientation": "sideways",
}
_CHOICES = {
    "punctuation_spacing": {"standard", "full", "half"},
    "line_start_opening": {"half", "full"},
    "line_end_closing": {"half", "full"},
    "kinsoku": {"ja_standard", "none"},
    "line_adjustment": {"compress", "none", "justify"},
    "latin_orientation": {"sideways", "upright"},
}
_LIMITS = {
    "latin_spacing_em": (0.0, 1.0),
    "compression_limit_em": (0.0, 1.0),
    "expansion_limit_em": (0.0, 2.0),
    "first_line_indent_em": (0.0, 10.0),
}


def normalize_typography(raw: Any) -> dict[str, Any]:
    """Return independent settings, retaining extensions and legacy opt-out."""
    source = raw if isinstance(raw, Mapping) else {}
    result = deepcopy(dict(source))
    for key, default in _DEFAULTS.items():
        value = source.get(key, default)
        if key in _CHOICES:
            value = value if isinstance(value, str) and value in _CHOICES[key] else default
        elif key in _LIMITS:
            value = finite_number(value, default, *_LIMITS[key])
        elif not isinstance(value, bool):
            value = default
        result[key] = value
    if "custom_rules" in source:
        result["custom_rules"] = normalize_rules(source["custom_rules"])
    return result


def typography_from_payload(payload: Any) -> dict[str, Any]:
    """Read the optional object-level ``layout.japanese`` settings."""
    layout = payload.get("layout") if isinstance(payload, Mapping) else None
    raw = layout.get("japanese") if isinstance(layout, Mapping) else None
    return normalize_typography(raw)


def has_typography(payload: Any) -> bool:
    """Whether character/paragraph formatting or annotations need shared layout."""
    return has_japanese_layout(payload) or bool(color_runs(payload))


def has_japanese_layout(payload: Any) -> bool:
    """Color-only edits must retain the legacy line and glyph geometry."""
    if (
        typography_from_payload(payload)["enabled"]
        or has_font_overrides(payload)
        or bool(geometry_runs(payload))
        or has_frame_flow(payload)
        or has_paragraph_styles(payload)
    ):
        return True
    document = payload.get("document") if isinstance(payload, Mapping) else None
    if not isinstance(document, Mapping):
        return False
    text = document.get("text", payload.get("content", ""))
    if not isinstance(text, str):
        return False
    return any(normalize_annotations(document.get(key), text, kind=key) for key in ANNOTATION_KEYS)
