"""Strict, discoverable inputs for current-format comic panel edits."""

from .vector_editing import ID, schema

ACTIONS = (
    "create",
    "template",
    "split",
    "merge",
    "delete",
    "resize",
    "incline",
    "assign_content",
    "place_image",
    "fit_content",
    "rasterize_edge",
    "restore_edge",
)
OBJECT = {"type": "object", "additionalProperties": True}
PROPERTIES = {
    "action": {"enum": list(ACTIONS)},
    "panelId": ID,
    "secondaryPanelId": ID,
    "templateId": {"enum": ["single", "2v", "2h", "3", "4", "yonkoma", "6"]},
    "layout": OBJECT,
    "gutterPx": {"type": "number", "minimum": 0},
    "marginPx": {"type": "number", "minimum": 0},
    "orientation": {"enum": ["vertical", "horizontal"]},
    "ratio": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
    "count": {"type": "integer", "minimum": 2, "maximum": 8},
    "allocations": {"type": "object", "additionalProperties": ID},
    "imagePolicies": {
        "type": "object",
        "additionalProperties": {"enum": ["move", "copy", "shared"]},
    },
    "disposition": {"enum": ["keep", "delete"]},
    "edgeId": ID,
    "skew": {"type": "number", "minimum": -0.9, "maximum": 0.9},
    "layerId": ID,
    "assetId": ID,
    "name": {"type": "string", "minLength": 1, "maxLength": 256},
    "fitPolicy": {"enum": ["cover", "contain"]},
}
FIELDS = {
    "create": {"templateId", "layout", "gutterPx", "marginPx"},
    "template": {"templateId", "layout", "gutterPx", "marginPx", "allocations"},
    "split": {"panelId", "orientation", "ratio", "count", "allocations", "imagePolicies"},
    "merge": {"panelId", "secondaryPanelId"},
    "delete": {"panelId", "disposition"},
    "resize": {"panelId", "layout", "ratio", "gutterPx", "marginPx"},
    "incline": {"panelId", "edgeId", "skew"},
    "assign_content": {"panelId", "layerId", "fitPolicy"},
    "place_image": {"panelId", "assetId", "fitPolicy", "name"},
    "fit_content": {"panelId", "layerId", "fitPolicy"},
    "rasterize_edge": {"panelId", "edgeId"},
    "restore_edge": {"panelId", "edgeId"},
}
REQUIRED = {
    "create": (),
    "template": (),
    "split": ("panelId", "orientation"),
    "merge": ("panelId", "secondaryPanelId"),
    "delete": ("panelId", "disposition"),
    "resize": ("panelId",),
    "incline": ("panelId", "edgeId", "skew"),
    "assign_content": ("panelId", "layerId"),
    "place_image": ("panelId", "assetId"),
    "fit_content": ("panelId", "layerId", "fitPolicy"),
    "rasterize_edge": ("panelId", "edgeId"),
    "restore_edge": ("panelId", "edgeId"),
}
PREVIEW_SCHEMA = schema(PROPERTIES, ["action"])


def input_error(arguments):  # noqa: PLR0911 - one actionable rejection per input condition
    action = arguments.get("action")
    if action not in FIELDS:
        return "unsupported panel action"
    if set(arguments) - FIELDS[action] - {"action", "expectedRevision"}:
        return "arguments do not belong to this panel action"
    if any(key not in arguments for key in REQUIRED[action]):
        return "required panel action argument is missing"
    if "layout" in arguments and {"templateId", "ratio"} & arguments.keys():
        return "choose a layout payload or a template/ratio, not both"
    if action == "template" and not {"templateId", "layout"} & arguments.keys():
        return "template requires templateId or layout"
    if action == "resize" and not {"layout", "ratio", "gutterPx", "marginPx"} & arguments.keys():
        return "resize requires a layout, ratio, gutter, or margin"
    return ""
