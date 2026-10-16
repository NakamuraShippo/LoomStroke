"""Canvas Agent changeset schema, validation, and preview helpers."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from .ai_effect_transaction import (
    AIEffectTransactionError,
    ai_effect_transaction_summary,
    operation_has_valid_ai_effect_apply_transaction,
    validate_ai_effect_apply_transaction,
)
from .canvas_shapes import (
    is_supported_shape_type,
    shape_type_from_operation,
    supported_shape_types,
)
from .safety import scrub_secrets
from .types import McpStructuredError, ReasonCode

CHANGESET_SCHEMA_VERSION = "canvas_changeset.v1"
VALIDATION_SCHEMA_VERSION = "canvas_changeset_validation.v1"
PREVIEW_SCHEMA_VERSION = "canvas_changeset_preview.v1"

MAX_OPERATION_COUNT = 20
MAX_STRING_PAYLOAD_LENGTH = 16_000
APPROVAL_TICKET_TTL_MINUTES = 15

ALLOWED_OPERATIONS = frozenset(
    {
        "create_layer",
        "duplicate_layer",
        "rename_layer",
        "set_layer_opacity",
        "set_layer_blend_mode",
        "add_text_object",
        "add_vector_shape",
        "import_asset_as_layer",
        "submit_generation_to_new_layer",
        "apply_ai_effect_preset",
        "panel_frame_edit",
        "create_selection_preview",
        "apply_selection_mask",
    }
)

FORBIDDEN_OPERATIONS = frozenset(
    {
        "direct_pixel_edit_from_prompt",
        "replace_document_image",
        "mutate_canvas_private_state",
        "write_lstroke_schema",
        "delete_layer_without_confirmation",
        "flatten_all_layers",
        "merge_destructive_without_duplicate",
        "send_full_document_to_external_llm",
    }
)

_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "session_token",
    "token",
)
_SAFE_SECRET_METADATA_KEYS = frozenset(
    {
        "credentialreadperformed",
        "credentialvaluesincluded",
        "approvalticket",
        "ticketid",
    }
)


def build_canvas_changeset(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    existing_asset_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a CanvasChangeset draft from a CanvasEditPlan."""

    if str(plan.get("schemaVersion") or "") != "canvas_edit_plan.v1":
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'plan' must be a canvas_edit_plan.v1 object.",
            details={"schemaVersion": plan.get("schemaVersion")},
            code=-32602,
        )
    now = _now_iso()
    changeset_id = f"chg_{uuid.uuid4().hex[:12]}"
    target = _target_from_plan(plan, context)
    source_digest = str(plan.get("sourcePromptDigest") or "")
    operations = _operations_from_plan(
        plan,
        context,
        changeset_id=changeset_id,
        target=target,
        existing_asset_ids=set(existing_asset_ids),
    )
    blocked_reasons = _safe_reasons(plan.get("blockedReasons"))
    intent = plan.get("intent") if isinstance(plan.get("intent"), Mapping) else {}
    if str(intent.get("kind") or "") == "production_task":
        blocked_reasons.append(
            {
                "code": "production_recipe_handoff_required",
                "message": "High-level production tasks must use ProductionRecipe runner.",
                "action": "Use the plan handoff card and loomstroke_plan_production_job.",
                "recipeId": intent.get("recipeId"),
            }
        )
    status = "blocked" if blocked_reasons else ("draft" if operations else "noop")
    changeset = {
        "schemaVersion": CHANGESET_SCHEMA_VERSION,
        "changesetId": changeset_id,
        "sourcePlanId": plan.get("planId"),
        "runId": plan.get("runId"),
        "contextId": plan.get("contextId") or context.get("contextId"),
        "baseDocumentRevision": plan.get("contextDigest") or context.get("contextDigest"),
        "createdAt": now,
        "updatedAt": now,
        "status": status,
        "target": target,
        "operations": operations,
        "blockedReasons": blocked_reasons,
        "validation": {
            "schemaVersion": VALIDATION_SCHEMA_VERSION,
            "status": "not_validated",
            "errors": [],
            "warnings": [],
        },
        "preview": {
            "status": "not_created",
            "previewId": None,
        },
        "approval": {
            "required": bool(operations),
            "ticketId": None,
            "status": "not_issued",
        },
        "safety": {
            "rawPromptIncluded": False,
            "rawBinaryIncluded": False,
            "credentialReadPerformed": False,
            "externalModelCalled": False,
            "allowedOperations": sorted(ALLOWED_OPERATIONS),
            "forbiddenOperations": sorted(FORBIDDEN_OPERATIONS),
        },
        "sourcePromptDigest": source_digest,
    }
    return scrub_secrets(changeset)


def validate_canvas_changeset(
    changeset: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    existing_asset_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate a CanvasChangeset for preview/apply readiness."""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    asset_ids = set(existing_asset_ids)
    layer_map = _layer_map(context)
    selection_context = (
        context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
    )
    virtual_layer_ids: set[str] = set()

    if str(changeset.get("schemaVersion") or "") != CHANGESET_SCHEMA_VERSION:
        errors.append(
            _issue(
                "schema_version_invalid",
                "Changeset must use canvas_changeset.v1.",
                field="schemaVersion",
            )
        )

    base_revision = str(changeset.get("baseDocumentRevision") or "")
    current_revision = str(context.get("contextDigest") or "")
    if base_revision and current_revision and base_revision != current_revision:
        errors.append(
            _issue(
                "stale_revision",
                "Changeset base revision does not match the current edit context.",
                baseDocumentRevision=base_revision,
                currentDocumentRevision=current_revision,
            )
        )

    for issue in _payload_issues(changeset):
        errors.append(issue)

    operations_raw = changeset.get("operations")
    if not isinstance(operations_raw, list):
        errors.append(
            _issue(
                "operations_must_be_list",
                "Changeset operations must be a list.",
                field="operations",
            )
        )
        operations: list[Mapping[str, Any]] = []
    else:
        operations = [operation for operation in operations_raw if isinstance(operation, Mapping)]
        if len(operations) != len(operations_raw):
            errors.append(
                _issue(
                    "operation_must_be_object",
                    "Every changeset operation must be an object.",
                    field="operations",
                )
            )
        if len(operations_raw) > MAX_OPERATION_COUNT:
            errors.append(
                _issue(
                    "operation_count_exceeded",
                    "Changeset operation count exceeds the preview limit.",
                    maxOperations=MAX_OPERATION_COUNT,
                    operationCount=len(operations_raw),
                )
            )

    target_layer_id = _target_layer_id(changeset)
    for index, operation in enumerate(operations):
        op_name = _operation_name(operation)
        if op_name in FORBIDDEN_OPERATIONS:
            errors.append(
                _issue(
                    "disallowed_operation",
                    "Forbidden operation is not allowed in CanvasChangeset.",
                    index=index,
                    operation=op_name,
                )
            )
            continue
        if op_name not in ALLOWED_OPERATIONS:
            errors.append(
                _issue(
                    "unsupported_operation",
                    "Operation is not supported by the Section 3 preview MVP.",
                    index=index,
                    operation=op_name or "<missing>",
                )
            )
            continue
        _validate_operation(
            operation,
            index=index,
            layer_map=layer_map,
            virtual_layer_ids=virtual_layer_ids,
            asset_ids=asset_ids,
            selection_context=selection_context,
            target_layer_id=target_layer_id,
            errors=errors,
            warnings=warnings,
        )

    status = "blocked" if errors else "valid"
    return scrub_secrets(
        {
            "schemaVersion": VALIDATION_SCHEMA_VERSION,
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "operationCount": len(operations),
        }
    )


def build_canvas_changeset_preview(
    changeset: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    validation: Mapping[str, Any] | None = None,
    issue_ticket: bool = True,
) -> dict[str, Any]:
    """Build a non-mutating structured preview artifact."""

    validation_payload = dict(validation or validate_canvas_changeset(changeset, context))
    preview_id = f"prev_{uuid.uuid4().hex[:12]}"
    blocked_reasons = _preview_blocked_reasons(changeset, validation_payload)
    operations = _operation_list(changeset)
    before = _layer_stack_summary(context)
    after = _apply_ghost_operations(before, operations)
    mode = _preview_mode(operations)
    selection_preview = _selection_preview_summary(operations, context)
    ticket = (
        build_approval_ticket(changeset, preview_id)
        if issue_ticket and not blocked_reasons and operations
        else None
    )
    return scrub_secrets(
        {
            "schemaVersion": PREVIEW_SCHEMA_VERSION,
            "previewId": preview_id,
            "changesetId": changeset.get("changesetId"),
            "mode": "plan_only" if blocked_reasons else mode,
            "blocked": bool(blocked_reasons),
            "blockedReasons": blocked_reasons,
            "validation": validation_payload,
            "before": before,
            "after": after,
            "ghostLayers": _ghost_layers(after),
            "selectionPreview": selection_preview,
            "candidateStack": _preview_candidate_stack(
                operations,
                preview_id=preview_id,
                mode=mode,
                ghost_layers=_ghost_layers(after),
                run_id=str(changeset.get("runId") or ""),
            ),
            "approvalTicket": ticket,
            "warnings": list(validation_payload.get("warnings") or []),
            "documentMutation": "none",
            "historyMutation": "none",
            "assetWrite": "none",
        }
    )


def build_approval_ticket(
    changeset: Mapping[str, Any],
    preview_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=APPROVAL_TICKET_TTL_MINUTES)
    return {
        "ticketId": f"ticket_{uuid.uuid4().hex[:12]}",
        "changesetId": changeset.get("changesetId"),
        "previewId": preview_id,
        "changesetHash": canvas_changeset_hash(changeset),
        "baseDocumentRevision": changeset.get("baseDocumentRevision"),
        "scope": "canvas_changeset",
        "issuedAt": issued_at.isoformat(),
        "expiresAt": expires_at.isoformat(),
    }


def canvas_changeset_hash(changeset: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in scrub_secrets(dict(changeset)).items()
        if key not in {"validation", "preview", "approval", "status", "updatedAt"}
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _operations_from_plan(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    changeset_id: str,
    target: Mapping[str, Any],
    existing_asset_ids: set[str],
) -> list[dict[str, Any]]:
    del existing_asset_ids
    intent = plan.get("intent") if isinstance(plan.get("intent"), Mapping) else {}
    kind = str(intent.get("kind") or "inspect")
    canvas = context.get("canvas") if isinstance(context.get("canvas"), Mapping) else {}
    width = max(1, _int(canvas.get("width"), 1024))
    height = max(1, _int(canvas.get("height"), 768))
    target_layer_id = str(target.get("layerId") or "")
    prefix = changeset_id.removeprefix("chg_")
    if kind == "selection_edit":
        selection = (
            context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
        )
        bounds = _selection_bounds_tuple(selection.get("bounds"))
        if selection.get("hasSelection") and bounds is not None:
            return [
                {
                    "operationId": f"op_{prefix}_selection_preview",
                    "op": "create_selection_preview",
                    "selectionId": selection.get("selectionId"),
                    "selectionBounds": {
                        "x": bounds[0],
                        "y": bounds[1],
                        "width": bounds[2],
                        "height": bounds[3],
                    },
                    "targetLayerId": target_layer_id or None,
                    "label": "Selection scope preview",
                }
            ]
        return []
    if kind == "add_text":
        layer_id = f"layer_{prefix}_text"
        selection = (
            context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
        )
        rect, placement = _text_rect_for_placement(
            intent,
            selection,
            width=width,
            height=height,
        )
        text = str(
            intent.get("text") or ("Sign" if intent.get("objectHint") == "sign" else "Title")
        )[:2000]
        return [
            {
                "operationId": f"op_{prefix}_create_text_layer",
                "op": "create_layer",
                "layerId": layer_id,
                "name": "Canvas Agent Text",
                "layerType": "vector",
                "vectorKind": "text",
                "insertAboveLayerId": target_layer_id or None,
            },
            {
                "operationId": f"op_{prefix}_add_text",
                "op": "add_text_object",
                "layerId": layer_id,
                "text": text,
                "rect": rect,
                "style": {
                    "fontSize": max(18, min(96, round(width / 18))),
                    "color": "#111111",
                    "alignment": "center",
                },
                "editable": True,
                "placement": placement,
            },
        ]
    if kind == "style_adjust" and target_layer_id:
        layer_id = f"layer_{prefix}_tone"
        source_name = _target_layer_name(context, target_layer_id) or "Layer"
        return [
            {
                "operationId": f"op_{prefix}_duplicate",
                "op": "duplicate_layer",
                "sourceLayerId": target_layer_id,
                "newLayerId": layer_id,
                "name": f"{source_name} - Canvas Agent Tone",
            },
            {
                "operationId": f"op_{prefix}_blend",
                "op": "set_layer_blend_mode",
                "layerId": layer_id,
                "blendMode": "overlay",
            },
            {
                "operationId": f"op_{prefix}_opacity",
                "op": "set_layer_opacity",
                "layerId": layer_id,
                "opacity": 0.35,
            },
        ]
    return []


def _validate_operation(  # noqa: PLR0911, PLR0912, PLR0915
    operation: Mapping[str, Any],
    *,
    index: int,
    layer_map: Mapping[str, Mapping[str, Any]],
    virtual_layer_ids: set[str],
    asset_ids: set[str],
    selection_context: Mapping[str, Any],
    target_layer_id: str | None,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    op_name = _operation_name(operation)
    if op_name == "panel_frame_edit":
        from .panel_frame_changeset import operation_error  # noqa: PLC0415

        reason = operation_error(operation)
        if reason:
            errors.append(_issue("invalid_panel_frame_edit", reason, index=index))
        if operation.get("action") == "place_image" and operation.get("assetId") not in asset_ids:
            errors.append(_issue("missing_asset", "Panel image asset does not exist.", index=index))
        return
    if op_name == "create_layer":
        layer_id = _required_operation_str(operation, "layerId", index, errors)
        if layer_id:
            if layer_id in layer_map or layer_id in virtual_layer_ids:
                errors.append(_issue("layer_id_conflict", "Layer id already exists.", index=index))
            virtual_layer_ids.add(layer_id)
        return
    if op_name == "duplicate_layer":
        source_id = _required_operation_str(operation, "sourceLayerId", index, errors)
        new_id = _required_operation_str(operation, "newLayerId", index, errors)
        _validate_layer_ref(
            source_id,
            field="sourceLayerId",
            index=index,
            layer_map=layer_map,
            virtual_layer_ids=virtual_layer_ids,
            target_layer_id=target_layer_id,
            errors=errors,
        )
        if new_id:
            if new_id in layer_map or new_id in virtual_layer_ids:
                errors.append(_issue("layer_id_conflict", "Layer id already exists.", index=index))
            virtual_layer_ids.add(new_id)
        return
    if op_name in {"rename_layer", "set_layer_opacity", "set_layer_blend_mode"}:
        layer_id = _required_operation_str(operation, "layerId", index, errors)
        _validate_layer_ref(
            layer_id,
            field="layerId",
            index=index,
            layer_map=layer_map,
            virtual_layer_ids=virtual_layer_ids,
            target_layer_id=target_layer_id,
            errors=errors,
        )
        if op_name == "set_layer_opacity":
            opacity = operation.get("opacity")
            if not isinstance(opacity, int | float) or not 0.0 <= float(opacity) <= 1.0:
                errors.append(
                    _issue(
                        "opacity_out_of_range",
                        "Layer opacity must be between 0.0 and 1.0.",
                        index=index,
                    )
                )
        if op_name == "set_layer_blend_mode" and not str(operation.get("blendMode") or ""):
            errors.append(_issue("blend_mode_required", "Blend mode is required.", index=index))
        return
    if op_name in {"add_text_object", "add_vector_shape"}:
        layer_id = _required_operation_str(operation, "layerId", index, errors)
        _validate_layer_ref(
            layer_id,
            field="layerId",
            index=index,
            layer_map=layer_map,
            virtual_layer_ids=virtual_layer_ids,
            target_layer_id=target_layer_id,
            errors=errors,
        )
        if op_name == "add_vector_shape":
            shape_type = shape_type_from_operation(operation)
            if not is_supported_shape_type(shape_type):
                errors.append(
                    _issue(
                        "unsupported_shape_type",
                        "Shape type is not supported by the Shape Manager.",
                        index=index,
                        field="shapeType",
                        shapeType=shape_type,
                        supportedShapeTypes=list(supported_shape_types()),
                    )
                )
        return
    if op_name == "import_asset_as_layer":
        asset_id = _required_operation_str(operation, "assetId", index, errors)
        if asset_id and asset_id not in asset_ids:
            errors.append(
                _issue(
                    "asset_not_found",
                    "Referenced asset is not registered in the MCP service.",
                    index=index,
                    assetId=asset_id,
                )
            )
        new_id = str(operation.get("newLayerId") or "").strip()
        if new_id:
            if new_id in layer_map or new_id in virtual_layer_ids:
                errors.append(_issue("layer_id_conflict", "Layer id already exists.", index=index))
            virtual_layer_ids.add(new_id)
        return
    if op_name == "submit_generation_to_new_layer":
        asset_id = str(operation.get("assetId") or operation.get("selectedAssetId") or "").strip()
        if asset_id and asset_id not in asset_ids:
            errors.append(
                _issue(
                    "asset_not_found",
                    "Referenced generation candidate asset is not registered in the MCP service.",
                    index=index,
                    assetId=asset_id,
                )
            )
        new_id = _required_operation_str(operation, "newLayerId", index, errors)
        if new_id:
            if new_id in layer_map or new_id in virtual_layer_ids:
                errors.append(_issue("layer_id_conflict", "Layer id already exists.", index=index))
            virtual_layer_ids.add(new_id)
        if not asset_id:
            warnings.append(
                _issue(
                    "generation_candidate_asset_required_for_apply",
                    "Generation candidate operation is preview-only until assetId is selected.",
                    index=index,
                )
            )
        return
    if op_name == "apply_ai_effect_preset":
        layer_id = str(
            operation.get("layerId")
            or operation.get("targetLayerId")
            or operation.get("targetLayerRef")
            or ""
        ).strip()
        if not layer_id:
            errors.append(
                _issue(
                    "target_layer_required",
                    "AI Effect preset operation requires a target layer reference.",
                    index=index,
                )
            )
        else:
            _validate_layer_ref(
                layer_id,
                field="targetLayerRef",
                index=index,
                layer_map=layer_map,
                virtual_layer_ids=virtual_layer_ids,
                target_layer_id=target_layer_id,
                errors=errors,
            )
        if not str(operation.get("presetId") or "").strip():
            errors.append(
                _issue(
                    "preset_id_required",
                    "AI Effect preset id is required.",
                    index=index,
                )
            )
        raw_transaction = operation.get("aiEffectTransaction") or operation.get("transaction")
        if raw_transaction is not None:
            try:
                validate_ai_effect_apply_transaction(raw_transaction, operation=operation)
            except AIEffectTransactionError as exc:
                errors.append(
                    _issue(
                        "ai_effect_transaction_invalid",
                        "AI Effect transaction contract is invalid.",
                        index=index,
                        error=str(exc),
                    )
                )
        return
    if op_name == "create_selection_preview":
        _validate_selection_operation(
            operation,
            index=index,
            selection_context=selection_context,
            errors=errors,
            require_rect=False,
        )
        return
    if op_name == "apply_selection_mask":
        _validate_selection_operation(
            operation,
            index=index,
            selection_context=selection_context,
            errors=errors,
            require_rect=True,
        )


def _validate_layer_ref(
    layer_id: str | None,
    *,
    field: str,
    index: int,
    layer_map: Mapping[str, Mapping[str, Any]],
    virtual_layer_ids: set[str],
    target_layer_id: str | None,
    errors: list[dict[str, Any]],
) -> None:
    if not layer_id:
        return
    if layer_id in virtual_layer_ids:
        return
    layer = layer_map.get(layer_id)
    if layer is None:
        errors.append(
            _issue(
                "target_layer_missing",
                "Referenced layer does not exist in the current edit context.",
                index=index,
                field=field,
                layerId=layer_id,
            )
        )
        return
    if bool(layer.get("locked")):
        errors.append(
            _issue(
                "target_layer_locked",
                "Referenced layer is locked.",
                index=index,
                layerId=layer_id,
            )
        )
    if not bool(layer.get("visible", True)):
        errors.append(
            _issue(
                "target_layer_hidden",
                "Referenced layer is hidden.",
                index=index,
                layerId=layer_id,
            )
        )
    if target_layer_id and layer_id != target_layer_id:
        errors.append(
            _issue(
                "non_target_mutation",
                "Operation targets a live layer outside the changeset target.",
                index=index,
                layerId=layer_id,
                targetLayerId=target_layer_id,
            )
        )


def _validate_selection_operation(
    operation: Mapping[str, Any],
    *,
    index: int,
    selection_context: Mapping[str, Any],
    errors: list[dict[str, Any]],
    require_rect: bool,
) -> None:
    if require_rect:
        rect = _operation_rect_tuple(operation.get("rect"))
        if rect is None:
            errors.append(
                _issue(
                    "selection_rect_required",
                    "Selection apply operation requires a valid rect.",
                    index=index,
                    field="rect",
                )
            )
        operation_name = str(operation.get("operation") or "replace").strip().lower()
        if operation_name not in {"replace", "add", "subtract", "intersect"}:
            errors.append(
                _issue(
                    "selection_operation_invalid",
                    "Selection operation must be replace/add/subtract/intersect.",
                    index=index,
                    operation=operation_name,
                )
            )
    elif not selection_context.get("hasSelection"):
        errors.append(
            _issue(
                "selection_required",
                "Selection preview requires an active selection.",
                index=index,
            )
        )

    operation_selection_id = str(operation.get("selectionId") or "").strip()
    context_selection_id = str(selection_context.get("selectionId") or "").strip()
    if (
        operation_selection_id
        and context_selection_id
        and operation_selection_id != context_selection_id
    ):
        errors.append(
            _issue(
                "stale_selection",
                "Selection id does not match the current edit context.",
                index=index,
                selectionId=operation_selection_id,
                currentSelectionId=context_selection_id,
            )
        )

    expected_bounds = _selection_bounds_tuple(
        operation.get("selectionBounds") or operation.get("baseSelectionBounds")
    )
    current_bounds = _selection_bounds_tuple(selection_context.get("bounds"))
    if (
        expected_bounds is not None
        and current_bounds is not None
        and expected_bounds != current_bounds
    ):
        errors.append(
            _issue(
                "stale_selection",
                "Selection bounds do not match the current edit context.",
                index=index,
                selectionBounds=expected_bounds,
                currentSelectionBounds=current_bounds,
            )
        )


def _apply_ghost_operations(  # noqa: PLR0912, PLR0915
    before: Mapping[str, Any],
    operations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    layers = [dict(layer) for layer in before.get("layers", []) if isinstance(layer, Mapping)]
    by_id = {str(layer.get("layerId") or ""): layer for layer in layers}
    ghost_objects: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        op_name = _operation_name(operation)
        if op_name == "create_layer":
            layer_id = str(operation.get("layerId") or "")
            layer = {
                "layerId": layer_id,
                "name": str(operation.get("name") or "Canvas Agent Layer"),
                "visible": True,
                "opacity": 1.0,
                "blendMode": "normal",
                "layerType": str(operation.get("layerType") or "raster"),
                "vectorKind": str(operation.get("vectorKind") or "draw"),
                "ghost": True,
                "source": "canvas_changeset_preview",
            }
            layers.append(layer)
            by_id[layer_id] = layer
        elif op_name == "duplicate_layer":
            source_id = str(operation.get("sourceLayerId") or "")
            new_id = str(operation.get("newLayerId") or "")
            source = by_id.get(source_id, {})
            layer = {
                **dict(source),
                "layerId": new_id,
                "name": str(operation.get("name") or f"{source.get('name', 'Layer')} Copy"),
                "ghost": True,
                "sourceLayerId": source_id,
                "source": "canvas_changeset_preview",
            }
            layers.append(layer)
            by_id[new_id] = layer
        elif op_name == "rename_layer":
            layer = by_id.get(str(operation.get("layerId") or ""))
            if layer is not None:
                layer["name"] = str(operation.get("name") or layer.get("name") or "")
                layer["ghostMutation"] = True
        elif op_name == "set_layer_opacity":
            layer = by_id.get(str(operation.get("layerId") or ""))
            if layer is not None:
                layer["opacity"] = float(operation.get("opacity") or 0.0)
                layer["ghostMutation"] = True
        elif op_name == "set_layer_blend_mode":
            layer = by_id.get(str(operation.get("layerId") or ""))
            if layer is not None:
                layer["blendMode"] = str(operation.get("blendMode") or "normal")
                layer["ghostMutation"] = True
        elif op_name in {"add_text_object", "add_vector_shape"}:
            layer_id = str(operation.get("layerId") or "")
            ghost_objects.setdefault(layer_id, []).append(
                {
                    "operation": op_name,
                    "editable": bool(operation.get("editable", True)),
                    "rect": scrub_secrets(operation.get("rect") or {}),
                    "style": scrub_secrets(operation.get("style") or {}),
                    "textIncluded": op_name == "add_text_object",
                    "shapeType": operation.get("shapeType"),
                }
            )
        elif op_name == "import_asset_as_layer":
            layer_id = str(operation.get("newLayerId") or f"layer_{uuid.uuid4().hex[:8]}")
            layer = {
                "layerId": layer_id,
                "name": str(operation.get("name") or "Imported Asset"),
                "visible": True,
                "opacity": 1.0,
                "blendMode": "normal",
                "layerType": "raster",
                "ghost": True,
                "assetId": operation.get("assetId"),
                "source": "canvas_changeset_preview",
            }
            layers.append(layer)
            by_id[layer_id] = layer
        elif op_name == "submit_generation_to_new_layer":
            layer_id = str(operation.get("newLayerId") or f"layer_{uuid.uuid4().hex[:8]}")
            asset_id = str(operation.get("assetId") or operation.get("selectedAssetId") or "")
            layer = {
                "layerId": layer_id,
                "name": str(operation.get("name") or "Generated Candidate"),
                "visible": True,
                "opacity": float(operation.get("opacity") or 1.0),
                "blendMode": str(operation.get("blendMode") or "normal"),
                "layerType": "raster",
                "ghost": True,
                "assetId": asset_id or None,
                "candidateId": operation.get("candidateId"),
                "source": "canvas_generation_candidate",
            }
            layers.append(layer)
            by_id[layer_id] = layer
        elif op_name == "apply_ai_effect_preset":
            layer_id = str(
                operation.get("layerId")
                or operation.get("targetLayerId")
                or operation.get("targetLayerRef")
                or ""
            )
            layer = by_id.get(layer_id)
            if layer is not None:
                apply_supported = operation_has_valid_ai_effect_apply_transaction(operation)
                raw_transaction = operation.get("aiEffectTransaction") or operation.get(
                    "transaction"
                )
                transaction_summary = None
                if apply_supported and isinstance(raw_transaction, Mapping):
                    transaction_summary = ai_effect_transaction_summary(raw_transaction)
                layer["ghostMutation"] = True
                layer["aiEffectPresetPreview"] = {
                    "presetId": operation.get("presetId"),
                    "params": scrub_secrets(operation.get("params") or {}),
                    "applySupported": apply_supported,
                    "handoffTool": "loomstroke_apply_ai_effect_preset",
                    "transaction": transaction_summary,
                }
        elif op_name in {"create_selection_preview", "apply_selection_mask"}:
            continue
    for layer_id, objects in ghost_objects.items():
        if layer_id in by_id:
            by_id[layer_id]["ghostObjects"] = objects
            by_id[layer_id]["ghost"] = True
    return {
        "baseDocumentRevision": before.get("baseDocumentRevision"),
        "layers": layers,
        "operationCount": len(operations),
    }


def _payload_issues(value: Any, path: str = "$") -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if isinstance(value, bytes | bytearray | memoryview):
        issues.append(
            _issue("binary_payload", "Binary payload is not allowed in CanvasChangeset.", path=path)
        )
        return issues
    if isinstance(value, str) and len(value) > MAX_STRING_PAYLOAD_LENGTH:
        issues.append(
            _issue(
                "large_string_payload",
                "String payload exceeds the CanvasChangeset limit.",
                path=path,
                maxLength=MAX_STRING_PAYLOAD_LENGTH,
            )
        )
        return issues
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered not in _SAFE_SECRET_METADATA_KEYS and any(
                part in lowered for part in _SECRET_KEY_PARTS
            ):
                issues.append(
                    _issue(
                        "secret_payload",
                        "Secret-shaped field is not allowed in CanvasChangeset.",
                        path=f"{path}.{key_text}",
                    )
                )
                continue
            issues.extend(_payload_issues(item, f"{path}.{key_text}"))
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            issues.extend(_payload_issues(item, f"{path}[{index}]"))
    return issues


def _layer_stack_summary(context: Mapping[str, Any]) -> dict[str, Any]:
    layers = [
        {
            "layerId": str(layer.get("layerId") or ""),
            "name": str(layer.get("name") or ""),
            "visible": bool(layer.get("visible", True)),
            "opacity": float(layer.get("opacity", 1.0)),
            "blendMode": str(layer.get("blendMode") or "normal"),
            "layerType": str(layer.get("layerType") or "raster"),
            "vectorKind": str(layer.get("vectorKind") or "draw"),
            "locked": bool(layer.get("locked", False)),
            "ghost": False,
        }
        for layer in _context_layers(context)
    ]
    return {
        "baseDocumentRevision": context.get("contextDigest"),
        "canvas": scrub_secrets(context.get("canvas") or {}),
        "layers": layers,
    }


def _ghost_layers(stack: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(layer)
        for layer in stack.get("layers", [])
        if isinstance(layer, Mapping)
        and (bool(layer.get("ghost")) or bool(layer.get("ghostMutation")))
    ]


def _selection_preview_summary(
    operations: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
) -> dict[str, Any] | None:
    selection_ops = [
        operation
        for operation in operations
        if _operation_name(operation) in {"create_selection_preview", "apply_selection_mask"}
    ]
    if not selection_ops:
        return None
    selection = context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
    active_layer = (
        context.get("activeLayer") if isinstance(context.get("activeLayer"), Mapping) else {}
    )
    operation = selection_ops[0]
    bounds = _selection_bounds_tuple(
        operation.get("selectionBounds") or operation.get("rect") or selection.get("bounds")
    )
    return scrub_secrets(
        {
            "schemaVersion": "canvas_selection_preview.v1",
            "mode": "scope_highlight",
            "selectionId": operation.get("selectionId") or selection.get("selectionId"),
            "hasSelection": bool(selection.get("hasSelection")) or bounds is not None,
            "bounds": bounds,
            "targetLayerId": operation.get("targetLayerId") or active_layer.get("layerId"),
            "operation": _operation_name(operation),
            "label": operation.get("label") or "Selection scope preview",
            "rawMaskIncluded": False,
            "documentMutation": "none",
        }
    )


def _preview_blocked_reasons(
    changeset: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    reasons = _safe_reasons(changeset.get("blockedReasons"))
    for error in validation.get("errors") or []:
        if isinstance(error, Mapping):
            reasons.append(
                {
                    "code": str(error.get("code") or "validation_error"),
                    "message": str(error.get("message") or "Changeset validation failed."),
                    **{
                        key: scrub_secrets(value)
                        for key, value in error.items()
                        if key not in {"code", "message"}
                    },
                }
            )
    return scrub_secrets(reasons)


def _preview_mode(operations: Sequence[Mapping[str, Any]]) -> str:  # noqa: PLR0911
    names = {_operation_name(operation) for operation in operations}
    if not names:
        return "plan_only"
    if names <= {"create_selection_preview", "apply_selection_mask"}:
        return "scope_highlight"
    if "submit_generation_to_new_layer" in names:
        return "candidate_stack"
    if names & {"add_text_object", "add_vector_shape"}:
        return "text_vector_preview"
    if "import_asset_as_layer" in names:
        return "overlay"
    if "apply_ai_effect_preset" in names:
        return "before_after"
    return "ghost_layer"


def _candidate_label(operations: Sequence[Mapping[str, Any]]) -> str:  # noqa: PLR0911
    mode = _preview_mode(operations)
    if mode == "text_vector_preview":
        return "Editable text/vector preview"
    if mode == "overlay":
        return "Asset import preview"
    if mode == "ghost_layer":
        return "Ghost layer stack preview"
    if mode == "candidate_stack":
        return "Generated candidate layer preview"
    if mode == "before_after":
        return "AI Effect preset handoff preview"
    if mode == "scope_highlight":
        return "Selection scope highlight preview"
    return "Plan-only preview"


def _preview_candidate_stack(
    operations: Sequence[Mapping[str, Any]],
    *,
    preview_id: str,
    mode: str,
    ghost_layers: Sequence[Mapping[str, Any]],
    run_id: str,
) -> dict[str, Any] | list[dict[str, Any]]:
    if mode != "candidate_stack":
        return [
            {
                "candidateId": f"cand_{preview_id.removeprefix('prev_')}",
                "label": _candidate_label(operations),
                "mode": mode,
                "operationCount": len(operations),
                "ghostLayers": list(ghost_layers),
            }
        ]
    candidates: list[dict[str, Any]] = []
    selected_candidate_id: str | None = None
    for index, operation in enumerate(operations, start=1):
        if _operation_name(operation) != "submit_generation_to_new_layer":
            continue
        candidate_id = str(operation.get("candidateId") or f"cand_{index}").strip()
        if not candidate_id:
            candidate_id = f"cand_{index}"
        asset_id = str(operation.get("assetId") or operation.get("selectedAssetId") or "").strip()
        if selected_candidate_id is None:
            selected_candidate_id = candidate_id
        candidates.append(
            {
                "candidateId": candidate_id,
                "assetId": asset_id or None,
                "thumbnailAssetId": asset_id or None,
                "label": str(operation.get("name") or f"案 {index}"),
                "targetLayerHint": "new_layer",
                "recommendedOpacity": float(operation.get("opacity") or 1.0),
                "recommendedBlendMode": str(operation.get("blendMode") or "normal"),
                "provider": "generation",
                "size": {},
                "warnings": [],
                "mode": mode,
                "ghostLayers": list(ghost_layers),
            }
        )
    return {
        "schemaVersion": "canvas_candidate_stack.v1",
        "stackId": f"cand_stack_{preview_id.removeprefix('prev_')}",
        "runId": run_id or None,
        "source": "changeset_preview",
        "jobId": None,
        "candidates": candidates,
        "selectedCandidateId": selected_candidate_id,
        "privacy": {"externalGenerationUsed": True, "payloadOmitted": True},
    }


def _operation_list(changeset: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    operations = changeset.get("operations")
    if not isinstance(operations, list):
        return []
    return [operation for operation in operations if isinstance(operation, Mapping)]


def _operation_name(operation: Mapping[str, Any]) -> str:
    return str(operation.get("op") or operation.get("kind") or "")


def _target_from_plan(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    target = plan.get("target") if isinstance(plan.get("target"), Mapping) else {}
    document = context.get("document") if isinstance(context.get("document"), Mapping) else {}
    page = context.get("page") if isinstance(context.get("page"), Mapping) else {}
    active = context.get("activeLayer") if isinstance(context.get("activeLayer"), Mapping) else {}
    return scrub_secrets(
        {
            "documentId": target.get("documentId") or document.get("documentId"),
            "pageId": target.get("pageId") or page.get("pageId"),
            "layerId": target.get("layerId") or active.get("layerId"),
            "hasSelection": bool(target.get("hasSelection")),
        }
    )


def _text_rect_for_placement(
    intent: Mapping[str, Any],
    selection: Mapping[str, Any],
    *,
    width: int,
    height: int,
) -> tuple[dict[str, float | int], str]:
    placement = str(intent.get("placement") or "").strip()
    selection_bounds = _selection_bounds_tuple(selection.get("bounds"))
    if (
        (
            placement == "selection_bounds"
            or (intent.get("selectionMode") == "active_selection" and not placement)
        )
        and selection.get("hasSelection")
        and selection_bounds is not None
    ):
        placement_name = (
            "selection_bounds" if placement == "selection_bounds" else "active_selection"
        )
        return (
            {
                "x": selection_bounds[0],
                "y": selection_bounds[1],
                "width": selection_bounds[2],
                "height": selection_bounds[3],
            },
            placement_name,
        )
    if placement == "canvas_center":
        rect_width = round(width * 0.52, 2)
        rect_height = round(max(40.0, height * 0.14), 2)
        return (
            {
                "x": round((width - rect_width) / 2, 2),
                "y": round((height - rect_height) / 2, 2),
                "width": rect_width,
                "height": rect_height,
            },
            "canvas_center",
        )
    return (
        {
            "x": round(width * 0.14, 2),
            "y": round(height * 0.08, 2),
            "width": round(width * 0.72, 2),
            "height": round(max(48.0, height * 0.12), 2),
        },
        "canvas_top" if placement in {"", "canvas_top"} else placement,
    )


def _target_layer_id(changeset: Mapping[str, Any]) -> str | None:
    target = changeset.get("target")
    if not isinstance(target, Mapping):
        return None
    layer_id = target.get("layerId")
    return str(layer_id) if isinstance(layer_id, str) and layer_id else None


def _selection_bounds_tuple(value: Any) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        width_value = value.get("width", value.get("w"))
        height_value = value.get("height", value.get("h"))
        parts = (
            value.get("x"),
            value.get("y"),
            width_value,
            height_value,
        )
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        parts = tuple(value[:4]) if len(value) >= 4 else ()
    else:
        return None
    if len(parts) != 4:
        return None
    x = _int(parts[0], 0)
    y = _int(parts[1], 0)
    width = _int(parts[2], 0)
    height = _int(parts[3], 0)
    if width <= 0 or height <= 0:
        return None
    return (x, y, width, height)


def _operation_rect_tuple(value: Any) -> tuple[int, int, int, int] | None:
    return _selection_bounds_tuple(value)


def _layer_map(context: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(layer.get("layerId") or ""): layer
        for layer in _context_layers(context)
        if str(layer.get("layerId") or "")
    }


def _context_layers(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    layers = context.get("layers")
    if not isinstance(layers, list):
        return []
    return [layer for layer in layers if isinstance(layer, Mapping)]


def _target_layer_name(context: Mapping[str, Any], layer_id: str) -> str | None:
    layer = _layer_map(context).get(layer_id)
    if layer is None:
        return None
    name = layer.get("name")
    return str(name) if name is not None else None


def _required_operation_str(
    operation: Mapping[str, Any],
    key: str,
    index: int,
    errors: list[dict[str, Any]],
) -> str | None:
    value = operation.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    errors.append(
        _issue(
            "operation_field_required",
            f"Operation field '{key}' is required.",
            index=index,
            field=key,
        )
    )
    return None


def _safe_reasons(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    reasons: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            reasons.append(scrub_secrets(dict(item)))
        else:
            reasons.append({"code": "blocked", "message": str(item)})
    return reasons


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **scrub_secrets(details)}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "ALLOWED_OPERATIONS",
    "CHANGESET_SCHEMA_VERSION",
    "FORBIDDEN_OPERATIONS",
    "PREVIEW_SCHEMA_VERSION",
    "VALIDATION_SCHEMA_VERSION",
    "build_approval_ticket",
    "build_canvas_changeset",
    "build_canvas_changeset_preview",
    "canvas_changeset_hash",
    "validate_canvas_changeset",
]
