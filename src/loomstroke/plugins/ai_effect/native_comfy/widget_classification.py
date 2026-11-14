"""Conservative UI classification from an already loaded ComfyUI node instance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from loomstroke.plugins.comfyui.object_info_spec import is_combo_head, split_input_spec

_WIDGET_TYPES = {"number", "slider", "toggle", "combo", "text", "customtext"}
_SCALAR_WIDGETS = {
    "INT": {"number", "slider"},
    "FLOAT": {"number", "slider"},
    "BOOLEAN": {"toggle"},
    "STRING": {"text", "customtext"},
}
_SEED_MODES = {"fixed", "randomize", "increment", "decrement"}


@dataclass(frozen=True)
class NodeWidgetClassification:
    kind: str
    reason: str = ""


def standard_seed_control(
    node: Mapping[str, Any], target: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Return a verified ComfyUI seed control linked to an INT widget."""
    name = target.get("name")
    schema = node.get("inputSchema", {})
    if not isinstance(name, str) or "seed" not in name.lower() or not isinstance(schema, Mapping):
        return None
    required, optional = schema.get("required", {}), schema.get("optional", {})
    if not isinstance(required, Mapping) or not isinstance(optional, Mapping):
        return None
    specs = {**required, **optional}
    head, options = split_input_spec(specs.get(name))
    linked = target.get("linkedWidgets")
    if (
        head != "INT"
        or options.get("control_after_generate") is not True
        or target.get("type") not in _SCALAR_WIDGETS["INT"]
        or not isinstance(linked, list)
        or len(linked) != 1
    ):
        return None
    control = linked[0]
    if not isinstance(control, Mapping):
        return None
    if (
        not isinstance(control.get("name"), str)
        or not control["name"].endswith("control_after_generate")
        or control.get("type") != "combo"
        or control.get("value") not in _SEED_MODES
    ):
        return None
    return control


def classify_node(  # noqa: PLR0911 - conservative fallbacks retain the native editor
    node: Mapping[str, Any],
) -> NodeWidgetClassification:
    """Never infer UI compatibility from the provider or backend input types alone.

    Unknown metadata, modified basic renderers, and additional native controls
    retain the frontend. This describes observed UI, not arbitrary JS behavior.
    """
    widgets = node.get("widgets")
    if not isinstance(widgets, list):
        return NodeWidgetClassification("unknown", "Missing widget information")
    for widget in widgets:
        if not isinstance(widget, Mapping):
            return NodeWidgetClassification("unknown", "Invalid widget information")
        if widget.get("type") not in _WIDGET_TYPES:
            return NodeWidgetClassification("custom", str(widget.get("name") or widget.get("type")))

    schema = node.get("inputSchema")
    if not isinstance(schema, Mapping):
        return NodeWidgetClassification("unknown", "Missing input schema")
    specs = {}
    for section in ("required", "optional"):
        inputs = schema.get(section, {})
        if not isinstance(inputs, Mapping):
            return NodeWidgetClassification("unknown", "Invalid input schema")
        specs.update(inputs)
    control_names = {
        control["name"]
        for widget in widgets
        if isinstance(widget, Mapping)
        if (control := standard_seed_control(node, widget)) is not None
    }
    for widget in widgets:
        name = widget.get("name")
        if name in control_names:
            if (
                widget.get("type") != "combo"
                or widget.get("dom")
                or widget.get("customDraw")
                or widget.get("customSerialize")
            ):
                return NodeWidgetClassification("unknown", str(name))
            continue
        head, options = split_input_spec(specs.get(name))
        allowed = {"combo"} if is_combo_head(head) else _SCALAR_WIDGETS.get(str(head), set())
        if widget.get("type") not in allowed or options.get("forceInput"):
            return NodeWidgetClassification("unknown", str(name))
        multiline = (
            head == "STRING"
            and options.get("multiline")
            and widget.get("type") == "customtext"
            and widget.get("domTag") == "TEXTAREA"
        )
        if not multiline and (
            widget.get("customSerialize") or widget.get("dom") or widget.get("customDraw")
        ):
            return NodeWidgetClassification("unknown", str(name))
    return NodeWidgetClassification("basic")
