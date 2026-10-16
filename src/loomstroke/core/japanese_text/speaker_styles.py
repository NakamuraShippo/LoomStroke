"""Document speaker style values and non-destructive text inheritance."""

import json
import re
from collections.abc import Mapping
from copy import deepcopy

from ..comic_metadata_codec import _merge_namespace, _read_namespace
from ..vector_text_layout import TextLayoutCalculator as Layout
from .character_styles import apply_character_override
from .named_styles import capture_style
from .paragraph_styles import apply_paragraph_style

NAMESPACE = "loomstroke.comictools.speaker_styles"
REFERENCE = "speaker_style_ref"
BASE_FIELDS = (
    "vertical",
    "rotate_half_width",
    "word_wrap",
    "line_height",
    "tracking",
    "padding_top",
    "padding_right",
    "padding_bottom",
    "padding_left",
)


def read_registry(root):
    return _read_namespace(root, NAMESPACE)


def merge_registry(root, updates):
    validate_registry(updates)
    return _merge_namespace(
        root,
        {
            **updates,
            "styles": updates.get("styles", []),
            "default_style_id": updates.get("default_style_id"),
        },
        NAMESPACE,
    )


def validate_registry(registry):
    if not isinstance(registry, Mapping):
        raise ValueError("Speaker styles must be a mapping.")
    try:
        json.dumps(registry, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Speaker styles must contain finite JSON values.") from exc
    styles = registry.get("styles", [])
    if not isinstance(styles, list):
        raise ValueError("Speaker styles must be a list.")
    ids, speakers = set(), set()
    for style in styles:
        if not isinstance(style, Mapping) or not isinstance(style.get("display_name", ""), str):
            raise ValueError("Invalid speaker style.")
        validate_color_tag(style.get("color_tag", ""))
        key = style.get("style_id")
        speaker = style.get("speaker", {})
        if not isinstance(key, str) or not key or key in ids or not isinstance(speaker, Mapping):
            raise ValueError("Speaker style identifiers must be unique.")
        ids.add(key)
        for field in ("speaker_id", "speaker_name"):
            value = speaker.get(field, "")
            if not isinstance(value, str):
                raise ValueError("Invalid speaker name or identifier.")
            if value:
                identity = (field, value)
                if identity in speakers:
                    raise ValueError("A speaker is already assigned to another style.")
                speakers.add(identity)
        text = style.get("text", {})
        if not isinstance(text, Mapping) or any(
            not isinstance(text.get(kind, {}), Mapping)
            for kind in ("character", "paragraph", "base")
        ):
            raise ValueError("Invalid speaker text style.")
        _validate_text_values(text)
    if registry.get("default_style_id") not in (None, "", *ids):
        raise ValueError("The default speaker style does not exist.")


def validate_color_tag(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
        raise ValueError("Color tags must be empty or #RRGGBB.")


def _validate_text_values(text):
    numeric = {
        "font_size_pt",
        "horizontal_scale",
        "vertical_scale",
        "baseline_shift_em",
        "before_space_em",
        "after_space_em",
        "line_height",
        "tracking",
        "paragraph_columns",
        "padding_top",
        "padding_right",
        "padding_bottom",
        "padding_left",
    }
    for section in text.values():
        if not isinstance(section, Mapping):
            continue
        for key, value in section.items():
            if key in numeric and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError("Invalid numeric speaker text style.")
    character = text.get("character", {})
    if any(
        key in character and not isinstance(character[key], str)
        for key in ("font_family", "font_style")
    ):
        raise ValueError("Invalid speaker font.")
    if not isinstance(text.get("paragraph", {}).get("japanese", {}), Mapping):
        raise ValueError("Invalid speaker paragraph typography.")


def resolve_style(registry, speaker_id, speaker_name):
    validate_registry(registry)
    styles = registry.get("styles", [])
    for field, value in (("speaker_id", speaker_id), ("speaker_name", speaker_name)):
        if value:
            match = next((s for s in styles if s.get("speaker", {}).get(field) == value), None)
            if match is not None:
                return deepcopy(match)
    return next(
        (deepcopy(s) for s in styles if s["style_id"] == registry.get("default_style_id")), None
    )


def capture_text_style(payload, index=0):
    defaults = Layout.text_style_defaults(payload)
    return {
        **{kind: capture_style(payload, index, kind) for kind in ("character", "paragraph")},
        "base": {
            key: deepcopy(payload.get(key, defaults.get(key)))
            for key in BASE_FIELDS
            if key in payload or key in defaults
        },
    }


def has_style_overrides(payload):
    reference = payload.get(REFERENCE)
    if not isinstance(reference, Mapping):
        return False
    values = reference.get("text", {})
    document = payload.get("document", {})
    for run in document.get("runs", []):
        if any(
            key in run and run[key] != value for key, value in values.get("character", {}).items()
        ):
            return True
    for record in document.get("paragraphs", []):
        current = record.get("style_overrides", {})
        if any(
            key in current and current[key] != value
            for key, value in values.get("paragraph", {}).items()
        ):
            return True
    return any(payload.get(key) != value for key, value in values.get("base", {}).items())


def apply_style(payload, style, *, clear_overrides=False):
    """Apply inherited ranges, retaining explicit differences from the previous style.

    The reference stores the applied values, so later text edits and run splits
    need no external object identity or array-index correspondence to retain
    overrides. Unknown metadata and annotations are never rewritten.
    """
    result = deepcopy(payload)
    reference = result.get(REFERENCE, {})
    previous = reference.get("text", {}) if isinstance(reference, Mapping) else {}
    values = style.get("text", {})
    length = len(Layout.text_content_from_payload(result))
    original = deepcopy(result.get("document", {}))
    _apply_base(result, values.get("base", {}), previous.get("base", {}), clear_overrides)
    for key, value in values.get("character", {}).items():
        if key in {"font_family", "font_style", "font_size_pt", "color_rgba"}:
            result[key] = deepcopy(value)
            result.setdefault("style_map", {}).setdefault("default", {})[key] = deepcopy(value)
        result = apply_character_override(result, 0, length, key, value)
    _apply_paragraph_defaults(result, values, clear_overrides)
    result = apply_paragraph_style(result, 0, length, values.get("paragraph", {}))
    if not clear_overrides:
        for run in original.get("runs", []):
            for key in values.get("character", {}):
                if key in run and run[key] != previous.get("character", {}).get(key):
                    result = apply_character_override(
                        result, run["start"], run["end"], key, run[key]
                    )
        for paragraph in original.get("paragraphs", []):
            overrides = {
                key: _inherit_value(
                    value, previous.get("paragraph", {}).get(key), values["paragraph"][key]
                )
                for key, value in paragraph.get("style_overrides", {}).items()
                if key in values.get("paragraph", {})
                and value != previous.get("paragraph", {}).get(key)
            }
            if overrides:
                result = apply_paragraph_style(
                    result, paragraph["start"], paragraph["end"], overrides
                )
    result[REFERENCE] = {"style_id": style["style_id"], "text": deepcopy(values)}
    return result


def _apply_paragraph_defaults(result, values, clear_overrides):
    for key in ("align", "vertical_flow"):
        if key in values.get("paragraph", {}):
            result[key] = deepcopy(values["paragraph"][key])
    if "japanese" in values.get("paragraph", {}):
        result.setdefault("layout", {})["japanese"] = deepcopy(values["paragraph"]["japanese"])
    if clear_overrides:
        for key in ("tracking", "line_height"):
            if key in values.get("base", {}):
                result[key + "_runs"] = []


def _inherit_value(current, previous, updated):
    if (
        isinstance(current, Mapping)
        and isinstance(previous, Mapping)
        and isinstance(updated, Mapping)
    ):
        result = deepcopy(updated)
        for key, value in current.items():
            if key not in previous or value != previous[key]:
                result[key] = _inherit_value(value, previous.get(key), updated.get(key))
        return result
    return deepcopy(current if current != previous else updated)


def _apply_base(result, values, previous, clear_overrides):
    for key, value in values.items():
        if key not in BASE_FIELDS:
            continue
        old = previous
        if clear_overrides or key not in old or result.get(key) == old[key]:
            result[key] = deepcopy(value)
            if key in ("vertical", "rotate_half_width", "tracking"):
                result.setdefault("style_map", {}).setdefault("default", {})[key] = deepcopy(value)
            if key.startswith("padding_"):
                result.setdefault("layout", {})[key] = deepcopy(value)
            if key == "word_wrap" and result.get("layout", {}).get("wrap_mode") != "shape":
                result.setdefault("layout", {})["wrap_mode"] = "word" if value else "none"
