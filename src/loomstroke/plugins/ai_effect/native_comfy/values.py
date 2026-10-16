"""Pure snapshot mapping for native ComfyUI views."""

from __future__ import annotations

import copy
from typing import Any


def validate_bridge_numbers(value: Any) -> None:
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        raise ValueError("Integer exceeds the safe JavaScript bridge range")
    if isinstance(value, dict):
        for item in value.values():
            validate_bridge_numbers(item)
    elif isinstance(value, list):
        for item in value:
            validate_bridge_numbers(item)


def parameter_bindings(state: Any) -> dict[str, dict[str, Any]]:
    snapshot = state.preset_snapshot
    mappings = snapshot.get("gateway_file", {}).get("node_mappings", {})
    return {
        entry["internal_id"]: copy.deepcopy(mappings[entry["internal_id"]])
        for entry in snapshot.get("manifest", {}).get("parameters", [])
        if entry.get("internal_id") in mappings and "input" in mappings[entry["internal_id"]]
    }


def snapshot_values(state: Any, output: dict[str, Any]) -> dict[str, Any]:
    values = copy.deepcopy(state.param_values)
    for key, ref in parameter_bindings(state).items():
        node = output.get(str(ref["node_id"]))
        if not isinstance(node, dict) or ref["input"] not in node.get("inputs", {}):
            raise ValueError(f"Published parameter is missing: {key}")
        values[key] = copy.deepcopy(node["inputs"][ref["input"]])
    validate_bridge_numbers(values)
    return values


def layer_document(state: Any) -> tuple[str, dict[str, Any]]:
    gateway = state.preset_snapshot.get("gateway_file", {})
    saved = state.param_ui_state.get("comfyui_native", {})
    workflow = saved.get("workflow") or gateway.get("native_workflow")
    if isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list):
        return "load", copy.deepcopy(workflow)
    return "loadApi", copy.deepcopy(gateway.get("workflow", {}))


def prompt_ui_state(ui: dict, values: dict, updates: dict | None) -> dict:
    """Keep composer drafts only while they match the native widget text."""
    result = copy.deepcopy(ui)
    for key, value in (updates or {}).items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = copy.deepcopy(value)
    for key, value in values.items():
        saved = result.get(key, {})
        live = saved.get("live_prompt", {}) if isinstance(saved, dict) else {}
        if live and live.get("composed_text") != value:
            saved.pop("live_prompt", None)
    return result
