"""Rich text patches using the existing character/paragraph/annotation contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from loomstroke.core.japanese_text.annotations import (
    ANNOTATION_KEYS,
    annotation_edit_error,
    annotation_selection,
    replace_annotation_range,
)
from loomstroke.core.japanese_text.character_geometry import GEOMETRY_LIMITS
from loomstroke.core.japanese_text.character_styles import apply_character_override
from loomstroke.core.japanese_text.named_styles import apply_named_style
from loomstroke.core.japanese_text.paragraph_styles import apply_paragraph_style
from loomstroke.core.japanese_text.typography import normalize_typography
from loomstroke.plugins.text_tool.common import (
    extract_text_content,
    merge_line_height_run,
    merge_tracking_run,
)
from loomstroke.plugins.text_tool.editing_metadata import replace_edited_text
from loomstroke.plugins.vector_tools.common import ensure_vector_element_ids

from .editing_common import finite_number, invalid

CHARACTER_FIELDS = {
    "font_family": {"type": "string"},
    "font_style": {"type": "string"},
    "font_size_pt": {"type": "number", "minimum": 1, "maximum": 512},
    "color_rgba": {
        "type": "array",
        "items": {"type": "integer", "minimum": 0, "maximum": 255},
        "minItems": 4,
        "maxItems": 4,
    },
    **{
        key: {"type": "number", "minimum": limits[1], "maximum": limits[2]}
        for key, limits in GEOMETRY_LIMITS.items()
    },
    "tracking": {"type": "number", "minimum": -100, "maximum": 100},
    "line_height": {"type": "number", "minimum": 0.1, "maximum": 10},
}
PARAGRAPH_FIELDS = {
    "align": {"enum": ["left", "center", "right", "justify"]},
    "vertical_flow": {"enum": ["left", "right"]},
    "paragraph_columns": {"type": "integer", "minimum": 0, "maximum": 32},
    "japanese": {"type": "object"},
}
SETTINGS_FIELDS = {
    **{key: value for key, value in CHARACTER_FIELDS.items() if key not in GEOMETRY_LIMITS},
    "vertical": {"type": "boolean"},
    "rotate_half_width": {"type": "boolean"},
    "word_wrap": {"type": "boolean"},
    "vertical_flow": PARAGRAPH_FIELDS["vertical_flow"],
    "align": PARAGRAPH_FIELDS["align"],
    "typography": {"type": "object"},
    **{
        key: {"type": "number", "minimum": 0, "maximum": 10000}
        for key in ("padding_top", "padding_right", "padding_bottom", "padding_left")
    },
}
FLOW_FIELDS = {
    "columns": {"type": "integer", "minimum": 1, "maximum": 32},
    "gap_em": {"type": "number", "minimum": 0, "maximum": 100},
    "grid_em": {"type": "number", "minimum": 0, "maximum": 100},
    "balance": {"type": "boolean"},
}


def validate_values(values, fields):  # noqa: PLR0912 - schema-type validation
    if not isinstance(values, dict) or set(values) - fields.keys():
        raise invalid("Unknown text property.", "properties")
    for key, value in values.items():
        definition = fields[key]
        kind = definition.get("type")
        if "enum" in definition and value not in definition["enum"]:
            raise invalid(f"Invalid '{key}'.", key)
        if kind in {"integer", "number"}:
            finite_number(
                value, key, minimum=definition.get("minimum"), maximum=definition.get("maximum")
            )
            if kind == "integer" and not isinstance(value, int):
                raise invalid(f"'{key}' must be an integer.", key)
        elif kind == "boolean" and not isinstance(value, bool):
            raise invalid(f"'{key}' must be boolean.", key)
        elif kind == "string" and not isinstance(value, str):
            raise invalid(f"'{key}' must be a string.", key)
        elif kind == "object" and not isinstance(value, dict):
            raise invalid(f"'{key}' must be an object.", key)
        elif kind == "array" and (
            not isinstance(value, list)
            or len(value) != 4
            or any(
                isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in value
            )
        ):
            raise invalid("color_rgba requires four RGBA8 channels.", key)


def patched_text(obj, args):  # noqa: PLR0912 - independent saved text format variants
    if not isinstance(obj.meta.get("text_tool"), dict):
        raise invalid("The target object is not text.", "objectId")
    result = replace_edited_text(
        obj, args.get("content", extract_text_content(obj.meta["text_tool"]))
    )
    payload = deepcopy(result.meta["text_tool"])
    settings = args.get("settings", {})
    validate_values(settings, SETTINGS_FIELDS)
    for key, value in settings.items():
        payload[key] = deepcopy(value)
        if key in {
            "font_family",
            "font_style",
            "font_size_pt",
            "color_rgba",
            "vertical",
            "rotate_half_width",
            "tracking",
        }:
            payload.setdefault("style_map", {}).setdefault("default", {})[key] = deepcopy(value)
        if key in {
            "vertical_flow",
            "line_height",
            "align",
            "word_wrap",
            "rotate_half_width",
        } or key.startswith("padding_"):
            payload.setdefault("layout", {})[key] = deepcopy(value)
        if key == "word_wrap":
            payload["layout"]["wrap_mode"] = "word" if value else "none"
        if key == "typography":
            payload.setdefault("layout", {})["japanese"] = normalize_typography(value)
    if "textFlow" in args:
        validate_values(args["textFlow"], FLOW_FIELDS)
        payload.setdefault("layout", {}).setdefault("text_flow", {}).update(
            deepcopy(args["textFlow"])
        )
    for change in args.get("ranges", []):
        text = extract_text_content(payload)
        start, end = change.get("start"), change.get("end")
        bounds = annotation_selection(text, start, end)
        if bounds is None or bounds != (start, end):
            raise invalid(
                "Text ranges must be nonempty grapheme boundaries in Python string coordinates.",
                "ranges",
            )
        character, paragraph = change.get("character", {}), change.get("paragraph", {})
        validate_values(character, CHARACTER_FIELDS)
        validate_values(paragraph, PARAGRAPH_FIELDS)
        for key, value in character.items():
            if key in {"tracking", "line_height"}:
                merge = merge_tracking_run if key == "tracking" else merge_line_height_run
                run_key = f"{key}_runs"
                payload[run_key] = merge(payload.get(run_key, []), start, end, value, len(text))
            else:
                payload = apply_character_override(payload, start, end, key, value)
        if paragraph:
            payload = apply_paragraph_style(payload, start, end, paragraph)
        if "namedStyle" in change:
            payload = apply_named_style(payload, start, end, change["namedStyle"])
        for annotation in change.get("annotations", []):
            kind = annotation.get("kind")
            options = annotation.get("options")
            if kind not in ANNOTATION_KEYS:
                raise invalid("Unknown annotation kind.", "annotations")
            error = annotation_edit_error(payload, kind, start, end, options)
            if error:
                raise invalid(error, "annotations")
            payload = replace_annotation_range(payload, kind, start, end, options)
    return ensure_vector_element_ids(
        replace(result, meta={**deepcopy(result.meta), "text_tool": payload})
    )
