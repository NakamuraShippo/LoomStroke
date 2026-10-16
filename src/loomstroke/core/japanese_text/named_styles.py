"""Named, object-local formatting presets stored in the existing style map."""

from collections.abc import Mapping
from copy import deepcopy

from ..vector_text_layout import TextLayoutCalculator as Layout
from .character_fonts import FONT_KEYS, font_runs
from .character_geometry import geometry_runs, geometry_values
from .character_styles import apply_character_override, color_at, color_runs
from .paragraph_styles import apply_paragraph_style, paragraph_style
from .typography import typography_from_payload


def named_styles(payload):
    catalog = payload.get("style_map", {})
    if not isinstance(catalog, Mapping):
        return {}
    return {
        key: deepcopy(value)
        for key, value in catalog.items()
        if key != "default"
        and isinstance(value, Mapping)
        and value.get("kind") in {"character", "paragraph"}
        and isinstance(value.get("values"), Mapping)
    }


def capture_style(payload, index, kind):
    defaults = Layout.text_style_defaults(payload)
    if kind == "paragraph":
        current = paragraph_style(payload, index)
        return {
            "paragraph_columns": current.get("paragraph_columns", 0),
            "align": current.get("align", Layout.text_align_from_payload(payload)),
            "vertical_flow": current.get(
                "vertical_flow", Layout.text_vertical_flow_from_payload(payload)
            ),
            "japanese": current.get("typography", typography_from_payload(payload)),
        }
    values = {key: defaults.get(key, payload.get(key)) for key in FONT_KEYS}
    values.update(
        next((v for a, b, v in geometry_runs(payload) if a <= index < b), geometry_values({}))
    )
    values.update(next((v for start, end, v in font_runs(payload) if start <= index < end), {}))
    values["color_rgba"] = list(
        color_at(
            color_runs(payload),
            index,
            defaults.get("color_rgba", payload.get("color_rgba", [0, 0, 0, 255])),
        )
    )
    return values


def apply_named_style(payload, start, end, request):
    result = deepcopy(payload)
    action, style_id = request.get("action"), request.get("id")
    catalog = result.setdefault("style_map", {})
    if not isinstance(catalog, dict):
        return result
    if action == "register":
        if isinstance(style_id, str) and style_id != "default" and style_id not in catalog:
            catalog[style_id] = deepcopy(request["definition"])
        return result
    if action == "delete":
        if style_id in named_styles(result):
            del catalog[style_id]
        return result
    kind = request.get("kind", "character")
    if action == "apply":
        definition = named_styles(result).get(style_id)
        if definition is None:
            return result
        kind, values = definition["kind"], definition["values"]
    elif action == "clear":
        values = capture_style({**result, "document": {}}, 0, kind)
    else:
        return result
    return _apply_values(result, start, end, kind, values)


def _apply_values(result, start, end, kind, values):
    if kind == "paragraph":
        result = apply_paragraph_style(result, start, end, values)
    else:
        for key, value in values.items():
            if key in FONT_KEYS or key == "color_rgba":
                result = apply_character_override(result, start, end, key, value)
    return result
