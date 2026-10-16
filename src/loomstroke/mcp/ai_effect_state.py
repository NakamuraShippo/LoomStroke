"""AI Effect layer state helpers for MCP tools."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .safety import scrub_secrets
from .serialization import to_jsonable
from .types import McpStructuredError, ReasonCode

_MISSING = object()
_VALID_TRIGGERS = {"manual", "on_change", "debounced"}
_VALID_OUTPUT_OVERRIDES = {"self", "new_layer_above"}


@dataclass
class AIEffectStateBackup:
    param_values: dict[str, Any]
    slot_refs: dict[str, Any]
    param_ui_state: dict[str, Any]
    trigger: str
    target_server_id: str | None
    output_mode_override: str | None


def has_ai_effect_state_update(arguments: Mapping[str, Any]) -> bool:
    return any(
        _value(arguments, camel, snake, default=_MISSING) is not _MISSING
        for camel, snake in (
            ("paramValues", "param_values"),
            ("slotRefs", "slot_refs"),
            ("paramUiState", "param_ui_state"),
            ("trigger", "trigger"),
            ("targetServerId", "target_server_id"),
            ("outputModeOverride", "output_mode_override"),
        )
    )


def find_live_ai_effect_layer(canvas: Any, layer_id: str) -> Any:
    finder = getattr(canvas, "_find_layer_by_id_tree", None)
    if not callable(finder):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "Canvas layer lookup is unavailable.",
            details={"layerId": layer_id},
        )
    layer = finder(layer_id)
    if layer is None or getattr(layer, "layer_type", "") != "ai_effect":
        raise McpStructuredError(
            ReasonCode.NOT_FOUND,
            "AI Effect layer was not found.",
            details={"layerId": layer_id},
        )
    if getattr(layer, "ai_effect", None) is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "AI Effect state is unavailable.",
            details={"layerId": layer_id},
        )
    return layer


def make_ai_effect_state_backup(state: Any) -> AIEffectStateBackup:
    return AIEffectStateBackup(
        param_values=copy.deepcopy(dict(getattr(state, "param_values", {}) or {})),
        slot_refs=copy.deepcopy(dict(getattr(state, "slot_refs", {}) or {})),
        param_ui_state=copy.deepcopy(dict(getattr(state, "param_ui_state", {}) or {})),
        trigger=str(getattr(state, "trigger", "manual") or "manual"),
        target_server_id=_optional_str(getattr(state, "target_server_id", None)),
        output_mode_override=_optional_str(getattr(state, "output_mode_override", None)),
    )


def restore_ai_effect_state_backup(
    canvas: Any,
    layer_id: str,
    state: Any,
    backup: AIEffectStateBackup,
) -> None:
    state.trigger = backup.trigger
    state.target_server_id = backup.target_server_id
    setter = getattr(canvas, "set_ai_effect_layer_state", None)
    if callable(setter):
        setter(
            layer_id,
            param_values=copy.deepcopy(backup.param_values),
            slot_refs=copy.deepcopy(backup.slot_refs),
            param_ui_state=copy.deepcopy(backup.param_ui_state),
        )
    output_setter = getattr(canvas, "set_ai_effect_layer_output_mode_override", None)
    if callable(output_setter):
        output_setter(layer_id, backup.output_mode_override)
    else:
        state.output_mode_override = backup.output_mode_override


def apply_ai_effect_state_update(
    canvas: Any,
    layer_id: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    layer = find_live_ai_effect_layer(canvas, layer_id)
    state = layer.ai_effect

    param_values = _updated_param_values(state, arguments)
    slot_refs = _updated_slot_refs(state, arguments)
    param_ui_state = _updated_param_ui_state(state, arguments)
    _apply_direct_state_fields(state, arguments)
    _write_canvas_state(canvas, layer_id, param_values, slot_refs, param_ui_state)
    _apply_output_override(canvas, layer_id, state, arguments)

    return {
        "layerId": layer_id,
        "updated": True,
        "argumentKeys": sorted(str(key) for key in arguments),
    }


def _updated_param_values(state: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    param_values = copy.deepcopy(dict(getattr(state, "param_values", {}) or {}))
    raw_param_values = _value(arguments, "paramValues", "param_values", default=_MISSING)
    if raw_param_values is _MISSING:
        return param_values
    if not isinstance(raw_param_values, Mapping):
        raise _invalid("paramValues must be an object.", "paramValues")
    if _replace_flag(arguments, "replaceParamValues", "replace_param_values"):
        param_values = {}
    param_values.update({str(key): copy.deepcopy(value) for key, value in raw_param_values.items()})
    return param_values


def _updated_slot_refs(state: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    slot_refs = copy.deepcopy(dict(getattr(state, "slot_refs", {}) or {}))
    raw_slot_refs = _value(arguments, "slotRefs", "slot_refs", default=_MISSING)
    if raw_slot_refs is _MISSING:
        return slot_refs
    if not isinstance(raw_slot_refs, Mapping):
        raise _invalid("slotRefs must be an object.", "slotRefs")
    if _replace_flag(arguments, "replaceSlotRefs", "replace_slot_refs"):
        slot_refs = {}
    for slot_id, raw_ref in raw_slot_refs.items():
        slot_refs[str(slot_id)] = _coerce_slot_ref(str(slot_id), raw_ref)
    return slot_refs


def _updated_param_ui_state(
    state: Any,
    arguments: Mapping[str, Any],
) -> dict[str, Any] | None:
    raw_param_ui_state = _value(arguments, "paramUiState", "param_ui_state", default=_MISSING)
    if raw_param_ui_state is _MISSING:
        return None
    if not isinstance(raw_param_ui_state, Mapping):
        raise _invalid("paramUiState must be an object.", "paramUiState")
    if _replace_flag(arguments, "replaceParamUiState", "replace_param_ui_state"):
        param_ui_state: dict[str, Any] = {}
    else:
        param_ui_state = copy.deepcopy(dict(getattr(state, "param_ui_state", {}) or {}))
    param_ui_state.update(
        {str(key): copy.deepcopy(value) for key, value in raw_param_ui_state.items()}
    )
    return param_ui_state


def _apply_direct_state_fields(state: Any, arguments: Mapping[str, Any]) -> None:
    raw_trigger = _value(arguments, "trigger", "trigger", default=_MISSING)
    if raw_trigger is not _MISSING:
        if raw_trigger not in _VALID_TRIGGERS:
            raise _invalid("trigger must be manual, on_change, or debounced.", "trigger")
        state.trigger = str(raw_trigger)

    raw_target_server = _value(arguments, "targetServerId", "target_server_id", default=_MISSING)
    if raw_target_server is not _MISSING:
        state.target_server_id = _optional_str(raw_target_server)


def _write_canvas_state(
    canvas: Any,
    layer_id: str,
    param_values: dict[str, Any],
    slot_refs: dict[str, Any],
    param_ui_state: dict[str, Any] | None,
) -> None:
    setter = getattr(canvas, "set_ai_effect_layer_state", None)
    if not callable(setter):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "Canvas AI Effect state setter is unavailable.",
            details={"layerId": layer_id},
        )
    ok = setter(
        layer_id,
        param_values=param_values,
        slot_refs=slot_refs,
        param_ui_state=param_ui_state,
    )
    if not ok:
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED,
            "AI Effect state update failed.",
            details={"layerId": layer_id},
        )


def _apply_output_override(
    canvas: Any,
    layer_id: str,
    state: Any,
    arguments: Mapping[str, Any],
) -> None:
    raw_output_override = _value(
        arguments,
        "outputModeOverride",
        "output_mode_override",
        default=_MISSING,
    )
    if raw_output_override is _MISSING:
        return
    output_override = _optional_str(raw_output_override)
    if output_override is not None and output_override not in _VALID_OUTPUT_OVERRIDES:
        raise _invalid(
            "outputModeOverride must be self, new_layer_above, or null.",
            "outputModeOverride",
        )
    output_setter = getattr(canvas, "set_ai_effect_layer_output_mode_override", None)
    if not callable(output_setter):
        state.output_mode_override = output_override
        return
    if not output_setter(layer_id, output_override):
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED,
            "AI Effect output mode override update failed.",
            details={"layerId": layer_id, "outputModeOverride": output_override},
        )


def build_ai_effect_layer_state_schema(
    layer: Any,
    *,
    include_gateway_file: bool = False,
    include_workflow: bool = False,
) -> dict[str, Any]:
    layer_id = str(_layer_value(layer, "id", "layerId") or "")
    state = _layer_value(layer, "ai_effect", "aiEffect")
    if state is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "AI Effect state is unavailable.",
            details={"layerId": layer_id},
        )
    ai_effect = _state_to_mapping(state)
    snapshot = ai_effect.get("preset_snapshot", {})
    if not isinstance(snapshot, Mapping):
        snapshot = {}
    manifest = snapshot.get("manifest", {})
    if not isinstance(manifest, Mapping):
        manifest = {}
    gateway_file = snapshot.get("gateway_file", {})
    if not isinstance(gateway_file, Mapping):
        gateway_file = {}

    param_values = ai_effect.get("param_values", {})
    if not isinstance(param_values, Mapping):
        param_values = {}
    param_ui_state = ai_effect.get("param_ui_state", {})
    if not isinstance(param_ui_state, Mapping):
        param_ui_state = {}
    slot_refs = ai_effect.get("slot_refs", {})
    if not isinstance(slot_refs, Mapping):
        slot_refs = {}

    payload: dict[str, Any] = {
        "layerId": layer_id,
        "name": str(_layer_value(layer, "name", "name") or ""),
        "presetId": str(ai_effect.get("preset_id") or ""),
        "gatewayId": str(ai_effect.get("gateway_id") or ""),
        "gatewayFileHash": str(ai_effect.get("gateway_file_hash") or ""),
        "metadata": (
            copy.deepcopy(manifest.get("metadata", {}))
            if isinstance(manifest.get("metadata", {}), Mapping)
            else {}
        ),
        "parameters": _parameter_records(manifest, param_values, param_ui_state),
        "paramValues": copy.deepcopy(dict(param_values)),
        "paramUiState": copy.deepcopy(dict(param_ui_state)),
        "inputSlots": _input_slot_records(manifest, slot_refs),
        "slotRefs": copy.deepcopy(dict(slot_refs)),
        "outputs": _output_records(manifest, ai_effect.get("output_mode_override")),
        "trigger": str(ai_effect.get("trigger") or "manual"),
        "keepHistory": bool(ai_effect.get("keep_history", False)),
        "outputModeOverride": ai_effect.get("output_mode_override"),
        "targetServerId": ai_effect.get("target_server_id"),
        "lastResult": copy.deepcopy(ai_effect.get("last_result")),
        "gateway": _gateway_summary(gateway_file),
    }
    if include_gateway_file:
        payload["gatewayFile"] = _gateway_file_payload(
            gateway_file,
            include_workflow=include_workflow,
        )
    return scrub_secrets(payload)


def _parameter_records(
    manifest: Mapping[str, Any],
    param_values: Mapping[str, Any],
    param_ui_state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw = manifest.get("parameters", [])
    if not isinstance(raw, list):
        return []
    records: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        internal_id = entry.get("internal_id")
        if not isinstance(internal_id, str) or not internal_id:
            continue
        record = copy.deepcopy(dict(entry))
        record["value"] = copy.deepcopy(param_values.get(internal_id))
        if internal_id in param_ui_state:
            record["uiState"] = copy.deepcopy(param_ui_state[internal_id])
        records.append(record)
    return records


def _input_slot_records(
    manifest: Mapping[str, Any],
    slot_refs: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw = manifest.get("input_slots", [])
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            internal_id = entry.get("internal_id")
            if not isinstance(internal_id, str) or not internal_id:
                continue
            seen.add(internal_id)
            record = copy.deepcopy(dict(entry))
            record["slotRef"] = copy.deepcopy(slot_refs.get(internal_id))
            records.append(record)
    for internal_id in sorted(str(key) for key in slot_refs if str(key) not in seen):
        records.append(
            {
                "internal_id": internal_id,
                "slotRef": copy.deepcopy(slot_refs[internal_id]),
            }
        )
    return records


def _output_records(manifest: Mapping[str, Any], output_override: Any) -> list[dict[str, Any]]:
    raw = manifest.get("outputs", [])
    if not isinstance(raw, list):
        return []
    records: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        record = copy.deepcopy(dict(entry))
        if isinstance(output_override, str) and output_override in _VALID_OUTPUT_OVERRIDES:
            record["effectiveMode"] = output_override
        else:
            record["effectiveMode"] = record.get("mode", "self")
        records.append(record)
    return records


def _gateway_summary(gateway_file: Mapping[str, Any]) -> dict[str, Any]:
    workflow = gateway_file.get("workflow")
    node_mappings = gateway_file.get("node_mappings")
    param_mappings = gateway_file.get("param_mappings")
    return {
        "hasWorkflow": isinstance(workflow, Mapping) and bool(workflow),
        "workflowNodeCount": len(workflow) if isinstance(workflow, Mapping) else 0,
        "nodeMappingCount": len(node_mappings) if isinstance(node_mappings, Mapping) else 0,
        "paramMappingCount": len(param_mappings) if isinstance(param_mappings, Mapping) else 0,
        "mappingKeys": sorted(
            key
            for key in ("node_mappings", "param_mappings", "input_mappings", "output_mappings")
            if isinstance(gateway_file.get(key), Mapping)
        ),
        "targetServerId": gateway_file.get("target_server_id"),
        "hasEndpointUrl": bool(gateway_file.get("endpoint_url")),
    }


def _gateway_file_payload(
    gateway_file: Mapping[str, Any],
    *,
    include_workflow: bool,
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(gateway_file))
    workflow = payload.get("workflow")
    if not include_workflow and isinstance(workflow, Mapping):
        payload["workflow"] = {
            "omitted": True,
            "nodeCount": len(workflow),
        }
    return payload


def _state_to_mapping(state: Any) -> dict[str, Any]:
    mapped = to_jsonable(state)
    if isinstance(mapped, Mapping):
        return copy.deepcopy(dict(mapped))
    return {}


def _coerce_slot_ref(slot_id: str, raw_ref: Any) -> Any:
    if raw_ref is None:
        return None
    from loomstroke.plugins.ai_effect.data_model import SlotRef  # noqa: PLC0415

    if isinstance(raw_ref, str):
        layer_id = raw_ref.strip()
        if not layer_id:
            return None
        return SlotRef(layer_id=layer_id, exclude_blend=False)
    if isinstance(raw_ref, Mapping):
        raw_layer_id = raw_ref.get("layerId", raw_ref.get("layer_id"))
        if not isinstance(raw_layer_id, str) or not raw_layer_id.strip():
            return None
        raw_exclude = raw_ref.get("excludeBlend", raw_ref.get("exclude_blend", False))
        return SlotRef(layer_id=raw_layer_id.strip(), exclude_blend=bool(raw_exclude))
    raise _invalid(
        "slotRefs values must be null, a layer id string, or an object.",
        f"slotRefs.{slot_id}",
    )


def _value(
    arguments: Mapping[str, Any],
    camel_name: str,
    snake_name: str,
    *,
    default: Any,
) -> Any:
    if camel_name in arguments:
        return arguments[camel_name]
    if snake_name in arguments:
        return arguments[snake_name]
    return default


def _replace_flag(arguments: Mapping[str, Any], camel_name: str, snake_name: str) -> bool:
    return bool(arguments.get(camel_name, arguments.get(snake_name, False)))


def _layer_value(layer: Any, attr_name: str, mapping_name: str) -> Any:
    if isinstance(layer, Mapping):
        return layer.get(mapping_name, layer.get(attr_name))
    return getattr(layer, attr_name, None)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid("value must be a string or null.", "value")
    stripped = value.strip()
    return stripped or None


def _invalid(message: str, argument: str) -> McpStructuredError:
    return McpStructuredError(
        ReasonCode.INVALID_ARGUMENT,
        message,
        details={"argument": argument},
    )


__all__ = [
    "AIEffectStateBackup",
    "apply_ai_effect_state_update",
    "build_ai_effect_layer_state_schema",
    "find_live_ai_effect_layer",
    "has_ai_effect_state_update",
    "make_ai_effect_state_backup",
    "restore_ai_effect_state_backup",
]
