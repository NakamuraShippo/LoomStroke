"""Strict persisted path/style input shapes for additive vector editing tools."""

POINT = {
    "type": "array",
    "items": {"type": "number", "minimum": -1e6, "maximum": 1e6},
    "minItems": 2,
    "maxItems": 2,
}
COLOR = {
    "type": "array",
    "items": {"type": "integer", "minimum": 0, "maximum": 255},
    "minItems": 3,
    "maxItems": 3,
}
PATHS = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "properties": {
            "anchors": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "pos": POINT,
                        "kind": {"enum": ["corner", "smooth"]},
                        "h_in": {"anyOf": [POINT, {"type": "null"}]},
                        "h_out": {"anyOf": [POINT, {"type": "null"}]},
                    },
                    "required": ["pos"],
                    "additionalProperties": False,
                },
            },
            "closed": {"type": "boolean"},
        },
        "required": ["anchors"],
        "additionalProperties": False,
    },
}
STYLE = {
    "type": "object",
    "properties": {
        "stroke_color": COLOR,
        "stroke_width": {"type": "number", "minimum": 0},
        "fill_enabled": {"type": "boolean"},
        "fill_color": {"anyOf": [COLOR, {"type": "null"}]},
        "join": {"enum": ["miter", "round", "bevel"]},
        "cap": {"enum": ["butt", "round", "square"]},
        "miter_limit": {"type": "number", "exclusiveMinimum": 0},
        "dash": {"type": "array", "items": {"type": "number", "minimum": 0}},
    },
    "additionalProperties": False,
}
