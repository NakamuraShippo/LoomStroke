"""``.lsfx`` manifest.json validator.

Spec reference:
- ``AIeffect_design/02_file_format/lsfx_specification_v1_1.md`` §manifest.json
- ``AIeffect_design/04_python_design/preset_authoring.md`` §5.1

Design:
- ``ok=False`` = hard-stop: preset cannot be installed or executed.
- ``ok=True`` with ``warnings`` = graceful degradation (unknown enum values
  etc.). Caller decides whether to surface warnings to UI.
- Gateway-specific file validation (``gateways/{gateway_id}.json``) is NOT
  handled here — that is the owning Gateway's responsibility
  (:meth:`AIGateway.validate_gateway_file`, Phase 5).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from loomstroke.plugins.ai_effect.preset import PresetValidationResult

VALID_CATEGORIES = frozenset({"analysis", "convert", "generate", "other"})
VALID_TRIGGERS = frozenset({"manual", "on_change", "debounced"})
VALID_OUTPUT_MODES = frozenset({"self", "new_layer_above"})
VALID_PARAM_TYPES = frozenset({"int", "float", "bool", "string", "enum"})
VALID_LAYER_TYPE_DEFAULT = "raster"


def validate_manifest(manifest: Any) -> PresetValidationResult:  # noqa: PLR0911, PLR0912, PLR0915
    """Validate a parsed ``manifest.json`` dict.

    Returns:
        PresetValidationResult with:
        - ok=False + reason for blocker issues
        - ok=True + warnings for acceptable but unusual values

    The function is defensive about input shape — a non-dict ``manifest``
    is reported cleanly instead of raising.
    """
    if not isinstance(manifest, dict):
        return PresetValidationResult(ok=False, reason="manifest_not_dict")

    warnings: list[str] = []

    # --- schema_version major check ---
    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, str):
        return PresetValidationResult(ok=False, reason="missing_schema_version")
    major = schema_version.split(".", 1)[0]
    if major != "1":
        return PresetValidationResult(
            ok=False,
            reason="schema_major_mismatch",
            detail=f"schema_version={schema_version}, supported major=1",
        )

    # --- preset_id UUID check ---
    preset_id = manifest.get("preset_id")
    if not isinstance(preset_id, str):
        return PresetValidationResult(ok=False, reason="missing_preset_id")
    try:
        UUID(preset_id)
    except (ValueError, TypeError):
        return PresetValidationResult(
            ok=False,
            reason="invalid_preset_id",
            detail=f"preset_id={preset_id!r} is not a valid UUID",
        )

    # --- Required top-level fields ---
    required_top = ("gateway", "metadata", "input_slots", "parameters", "outputs", "execution")
    for key in required_top:
        if key not in manifest:
            return PresetValidationResult(ok=False, reason=f"missing_{key}")

    gateway = manifest["gateway"]
    if not isinstance(gateway, str) or not gateway:
        return PresetValidationResult(ok=False, reason="invalid_gateway")

    # --- metadata.name required non-empty ---
    metadata = manifest["metadata"]
    if not isinstance(metadata, dict):
        return PresetValidationResult(ok=False, reason="metadata_not_dict")
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        return PresetValidationResult(ok=False, reason="empty_name")
    category = metadata.get("category")
    if category is not None and category not in VALID_CATEGORIES:
        warnings.append(f"unknown_category:{category}")

    # --- input_slots / parameters / outputs: internal_id uniqueness + shape ---
    for section in ("input_slots", "parameters", "outputs"):
        entries = manifest[section]
        if not isinstance(entries, list):
            return PresetValidationResult(ok=False, reason=f"{section}_not_list")
        seen: set[str] = set()
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                return PresetValidationResult(
                    ok=False,
                    reason=f"{section}_entry_not_dict",
                    detail=f"{section}[{idx}] is {type(entry).__name__}",
                )
            internal_id = entry.get("internal_id")
            if not isinstance(internal_id, str) or not internal_id:
                return PresetValidationResult(
                    ok=False,
                    reason=f"{section}_missing_internal_id",
                    detail=f"{section}[{idx}]",
                )
            if internal_id in seen:
                return PresetValidationResult(
                    ok=False,
                    reason=f"duplicate_internal_id_in_{section}",
                    detail=f"{internal_id!r} appears twice in {section}",
                )
            seen.add(internal_id)

    # --- parameters type + range + choices shape ---
    for idx, param in enumerate(manifest["parameters"]):
        ptype = param.get("type")
        if ptype not in VALID_PARAM_TYPES:
            return PresetValidationResult(
                ok=False,
                reason="invalid_parameter_type",
                detail=f"parameters[{idx}].type={ptype!r}",
            )
        if ptype == "enum":
            choices = param.get("choices")
            if not isinstance(choices, list) or not choices:
                return PresetValidationResult(
                    ok=False,
                    reason="enum_missing_choices",
                    detail=f"parameters[{idx}] with type=enum",
                )
        if ptype in ("int", "float"):
            rng = param.get("range")
            if rng is not None and not isinstance(rng, dict):
                return PresetValidationResult(
                    ok=False,
                    reason="numeric_range_not_dict",
                    detail=f"parameters[{idx}].range",
                )

    # --- parameter_groups referential integrity ---
    parameter_groups = manifest.get("parameter_groups", [])
    if not isinstance(parameter_groups, list):
        return PresetValidationResult(ok=False, reason="parameter_groups_not_list")
    group_names = set()
    for idx, grp in enumerate(parameter_groups):
        if not isinstance(grp, dict):
            return PresetValidationResult(
                ok=False,
                reason="parameter_group_not_dict",
                detail=f"parameter_groups[{idx}]",
            )
        gname = grp.get("name")
        if not isinstance(gname, str) or not gname:
            return PresetValidationResult(
                ok=False,
                reason="parameter_group_missing_name",
                detail=f"parameter_groups[{idx}]",
            )
        group_names.add(gname)
    for idx, param in enumerate(manifest["parameters"]):
        group_ref = param.get("group")
        if group_ref is not None and group_ref not in group_names:
            warnings.append(f"unknown_group:{group_ref}@parameters[{idx}]")

    # --- outputs mode enum ---
    for idx, output in enumerate(manifest["outputs"]):
        mode = output.get("mode")
        if mode not in VALID_OUTPUT_MODES:
            return PresetValidationResult(
                ok=False,
                reason="invalid_output_mode",
                detail=f"outputs[{idx}].mode={mode!r}",
            )

    # --- execution.trigger enum ---
    execution = manifest["execution"]
    if not isinstance(execution, dict):
        return PresetValidationResult(ok=False, reason="execution_not_dict")
    trigger = execution.get("trigger")
    if trigger not in VALID_TRIGGERS:
        return PresetValidationResult(
            ok=False,
            reason="invalid_trigger",
            detail=f"execution.trigger={trigger!r}",
        )

    return PresetValidationResult(ok=True, warnings=warnings)
