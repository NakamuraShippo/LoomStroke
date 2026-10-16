"""JSON-safe candidate list view model projection for SAM3 AI Select."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

CANDIDATE_LIST_VIEW_MODEL_SCHEMA_VERSION = "sam3.ai_select_candidate_list_view_model.v1"

_TOOL_ID = "sam3_ai_select"
_CANDIDATE_SELECT_KIND = "candidate_select"
_CANDIDATE_REJECT_KIND = "candidate_reject"
_CANDIDATE_REFINE_KIND = "candidate_refine"
_CANDIDATE_APPLY_LAYER_MASK_KIND = "candidate_apply_layer_mask"
_CANDIDATE_USE_INPAINT_MASK_KIND = "candidate_use_as_inpaint_mask"
_CANDIDATE_USE_BACKGROUND_GENERATION_MASK_KIND = "candidate_use_as_background_generation_mask"
_CANDIDATE_PREPARE_INPAINT_PREVIEW_REQUEST_KIND = "candidate_prepare_inpaint_preview_request"
_CANDIDATE_APPLY_CLIPPING_MASK_KIND = "candidate_apply_clipping_mask"
_CANDIDATE_CUT_TO_LAYER_KIND = "candidate_cut_to_new_layer"
_CANDIDATE_EXTRACT_FOREGROUND_KIND = "candidate_extract_foreground_to_new_layer"
_CANDIDATE_EXTRACT_BACKGROUND_KIND = "candidate_extract_background_to_new_layer"
_CANDIDATE_SAVE_MASK_ASSET_KIND = "candidate_save_mask_asset"
_ACTION_MISSING_BLOCKED_REASON = "candidate_action_missing"
_STYLE_KEYS = frozenset({"colorMode", "fillRgba", "outlineRgba", "opacity"})


def build_candidate_list_view_model(
    candidate_list_snapshot: Mapping[str, Any],
    context_actions_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Join candidate list items with row action state for future list UI."""

    selected_candidate_id = _optional_text(candidate_list_snapshot.get("selectedCandidateId"))
    action_lookup = _candidate_actions_by_id(context_actions_snapshot)
    rows = [
        row
        for item in _mapping_items(candidate_list_snapshot.get("items"))
        if (row := _candidate_row_payload(item, selected_candidate_id, action_lookup)) is not None
    ]

    return {
        "schemaVersion": CANDIDATE_LIST_VIEW_MODEL_SCHEMA_VERSION,
        "tool": _optional_text(candidate_list_snapshot.get("tool")) or _TOOL_ID,
        "candidateListSchemaVersion": _optional_text(candidate_list_snapshot.get("schemaVersion")),
        "contextActionsSchemaVersion": _optional_text(
            context_actions_snapshot.get("schemaVersion")
        ),
        "hasOverlayModel": bool(candidate_list_snapshot.get("hasOverlayModel", False)),
        "hasSession": bool(context_actions_snapshot.get("hasSession", False)),
        "candidateSetId": _optional_text(candidate_list_snapshot.get("candidateSetId")),
        "selectedCandidateId": selected_candidate_id,
        "selectedCandidateIndex": _optional_int(
            context_actions_snapshot.get("selectedCandidateIndex")
        ),
        "operation": _optional_text(context_actions_snapshot.get("operation")),
        "availableCandidateIds": _string_list(candidate_list_snapshot.get("availableCandidateIds")),
        "rejectedCandidateIds": _string_list(candidate_list_snapshot.get("rejectedCandidateIds")),
        "visibleCandidateIds": _string_list(candidate_list_snapshot.get("visibleCandidateIds")),
        "rows": rows,
        "warningCodes": _string_list(candidate_list_snapshot.get("warningCodes")),
        "previewSubmitStatus": _optional_text(candidate_list_snapshot.get("previewSubmitStatus")),
        "previewSubmitBlockedReasonCodes": _string_list(
            candidate_list_snapshot.get("previewSubmitBlockedReasonCodes")
        ),
        "previewSubmitDiffQaOk": _optional_bool(
            candidate_list_snapshot.get("previewSubmitDiffQaOk")
        ),
    }


def _candidate_row_payload(
    item: Mapping[str, Any],
    selected_candidate_id: str | None,
    action_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any] | None:
    candidate_id = _optional_text(item.get("candidateId"))
    if candidate_id is None:
        return None

    select_action = action_lookup.get((_CANDIDATE_SELECT_KIND, candidate_id))
    reject_action = action_lookup.get((_CANDIDATE_REJECT_KIND, candidate_id))
    refine_action = action_lookup.get((_CANDIDATE_REFINE_KIND, candidate_id))
    layer_mask_action = action_lookup.get((_CANDIDATE_APPLY_LAYER_MASK_KIND, candidate_id))
    inpaint_mask_action = action_lookup.get((_CANDIDATE_USE_INPAINT_MASK_KIND, candidate_id))
    protect_subject_action = action_lookup.get(
        (_CANDIDATE_USE_BACKGROUND_GENERATION_MASK_KIND, candidate_id)
    )
    preview_inpaint_action = action_lookup.get(
        (_CANDIDATE_PREPARE_INPAINT_PREVIEW_REQUEST_KIND, candidate_id)
    )
    clipping_mask_action = action_lookup.get((_CANDIDATE_APPLY_CLIPPING_MASK_KIND, candidate_id))
    cut_to_layer_action = action_lookup.get((_CANDIDATE_CUT_TO_LAYER_KIND, candidate_id))
    extract_foreground_action = action_lookup.get(
        (_CANDIDATE_EXTRACT_FOREGROUND_KIND, candidate_id)
    )
    extract_background_action = action_lookup.get(
        (_CANDIDATE_EXTRACT_BACKGROUND_KIND, candidate_id)
    )
    save_mask_asset_action = action_lookup.get((_CANDIDATE_SAVE_MASK_ASSET_KIND, candidate_id))
    select_state = _action_state_payload(select_action)
    reject_state = _action_state_payload(reject_action)
    refine_state = _action_state_payload(refine_action)
    layer_mask_state = _action_state_payload(layer_mask_action)
    inpaint_mask_state = _action_state_payload(inpaint_mask_action)
    protect_subject_state = _action_state_payload(protect_subject_action)
    preview_inpaint_state = _action_state_payload(preview_inpaint_action)
    clipping_mask_state = _action_state_payload(clipping_mask_action)
    cut_to_layer_state = _action_state_payload(cut_to_layer_action)
    extract_foreground_state = _action_state_payload(extract_foreground_action)
    extract_background_state = _action_state_payload(extract_background_action)
    save_mask_asset_state = _action_state_payload(save_mask_asset_action)
    raw_state = _optional_text(item.get("state"))
    selected = bool(select_state["checked"]) or candidate_id == selected_candidate_id
    if not selected and selected_candidate_id is None:
        selected = raw_state == "selected"

    return {
        "candidateId": candidate_id,
        "label": _optional_text(item.get("label")),
        "summaryText": _summary_text(candidate_id, item, select_action, reject_action),
        "score": _optional_float(item.get("score")),
        "rank": _optional_int(item.get("rank")),
        "bbox": _number_list(item.get("bbox")),
        "clippedBbox": _optional_number_list(item.get("clippedBbox")),
        "areaPx": _optional_int(item.get("areaPx")),
        "instanceIndex": _optional_int(item.get("instanceIndex")),
        "groupId": _optional_text(item.get("groupId")),
        "state": raw_state,
        "selected": selected,
        "visible": bool(item.get("visible", False)),
        "style": _style_payload(item.get("style")),
        "selectActionId": select_state["id"],
        "selectLabel": select_state["label"],
        "selectEnabled": select_state["enabled"],
        "selectChecked": select_state["checked"],
        "selectBlockedReasonCodes": select_state["blockedReasonCodes"],
        "rejectActionId": reject_state["id"],
        "rejectLabel": reject_state["label"],
        "rejectEnabled": reject_state["enabled"],
        "rejectChecked": reject_state["checked"],
        "rejectBlockedReasonCodes": reject_state["blockedReasonCodes"],
        "refineActionId": refine_state["id"],
        "refineLabel": refine_state["label"],
        "refineEnabled": refine_state["enabled"],
        "refineChecked": refine_state["checked"],
        "refineBlockedReasonCodes": refine_state["blockedReasonCodes"],
        "layerMaskActionId": layer_mask_state["id"],
        "layerMaskLabel": layer_mask_state["label"],
        "layerMaskEnabled": layer_mask_state["enabled"],
        "layerMaskChecked": layer_mask_state["checked"],
        "layerMaskBlockedReasonCodes": layer_mask_state["blockedReasonCodes"],
        "inpaintMaskActionId": inpaint_mask_state["id"],
        "inpaintMaskLabel": inpaint_mask_state["label"],
        "inpaintMaskEnabled": inpaint_mask_state["enabled"],
        "inpaintMaskChecked": inpaint_mask_state["checked"],
        "inpaintMaskBlockedReasonCodes": inpaint_mask_state["blockedReasonCodes"],
        "protectSubjectActionId": protect_subject_state["id"],
        "protectSubjectLabel": protect_subject_state["label"],
        "protectSubjectEnabled": protect_subject_state["enabled"],
        "protectSubjectChecked": protect_subject_state["checked"],
        "protectSubjectBlockedReasonCodes": protect_subject_state["blockedReasonCodes"],
        "previewInpaintActionId": preview_inpaint_state["id"],
        "previewInpaintLabel": preview_inpaint_state["label"],
        "previewInpaintEnabled": preview_inpaint_state["enabled"],
        "previewInpaintChecked": preview_inpaint_state["checked"],
        "previewInpaintBlockedReasonCodes": preview_inpaint_state["blockedReasonCodes"],
        "clippingMaskActionId": clipping_mask_state["id"],
        "clippingMaskLabel": clipping_mask_state["label"],
        "clippingMaskEnabled": clipping_mask_state["enabled"],
        "clippingMaskChecked": clipping_mask_state["checked"],
        "clippingMaskBlockedReasonCodes": clipping_mask_state["blockedReasonCodes"],
        "cutToLayerActionId": cut_to_layer_state["id"],
        "cutToLayerLabel": cut_to_layer_state["label"],
        "cutToLayerEnabled": cut_to_layer_state["enabled"],
        "cutToLayerChecked": cut_to_layer_state["checked"],
        "cutToLayerBlockedReasonCodes": cut_to_layer_state["blockedReasonCodes"],
        "extractForegroundActionId": extract_foreground_state["id"],
        "extractForegroundLabel": extract_foreground_state["label"],
        "extractForegroundEnabled": extract_foreground_state["enabled"],
        "extractForegroundChecked": extract_foreground_state["checked"],
        "extractForegroundBlockedReasonCodes": extract_foreground_state["blockedReasonCodes"],
        "extractBackgroundActionId": extract_background_state["id"],
        "extractBackgroundLabel": extract_background_state["label"],
        "extractBackgroundEnabled": extract_background_state["enabled"],
        "extractBackgroundChecked": extract_background_state["checked"],
        "extractBackgroundBlockedReasonCodes": extract_background_state["blockedReasonCodes"],
        "saveMaskAssetActionId": save_mask_asset_state["id"],
        "saveMaskAssetLabel": save_mask_asset_state["label"],
        "saveMaskAssetEnabled": save_mask_asset_state["enabled"],
        "saveMaskAssetChecked": save_mask_asset_state["checked"],
        "saveMaskAssetBlockedReasonCodes": save_mask_asset_state["blockedReasonCodes"],
    }


def _candidate_actions_by_id(
    context_actions_snapshot: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    actions: dict[tuple[str, str], Mapping[str, Any]] = {}
    for action in _mapping_items(context_actions_snapshot.get("actions")):
        kind = _optional_text(action.get("kind"))
        if kind not in {
            _CANDIDATE_SELECT_KIND,
            _CANDIDATE_REJECT_KIND,
            _CANDIDATE_REFINE_KIND,
            _CANDIDATE_APPLY_LAYER_MASK_KIND,
            _CANDIDATE_USE_INPAINT_MASK_KIND,
            _CANDIDATE_USE_BACKGROUND_GENERATION_MASK_KIND,
            _CANDIDATE_PREPARE_INPAINT_PREVIEW_REQUEST_KIND,
            _CANDIDATE_APPLY_CLIPPING_MASK_KIND,
            _CANDIDATE_CUT_TO_LAYER_KIND,
            _CANDIDATE_EXTRACT_FOREGROUND_KIND,
            _CANDIDATE_EXTRACT_BACKGROUND_KIND,
            _CANDIDATE_SAVE_MASK_ASSET_KIND,
        }:
            continue
        candidate_id = _optional_text(action.get("candidateId"))
        if candidate_id is None:
            continue
        actions[(kind, candidate_id)] = action
    return actions


def _action_state_payload(action: Mapping[str, Any] | None) -> dict[str, Any]:
    if action is None:
        return {
            "id": None,
            "label": None,
            "enabled": False,
            "checked": False,
            "blockedReasonCodes": [_ACTION_MISSING_BLOCKED_REASON],
        }
    return {
        "id": _optional_text(action.get("id")),
        "label": _optional_text(action.get("label")),
        "enabled": bool(action.get("enabled", False)),
        "checked": bool(action.get("checked", False)),
        "blockedReasonCodes": _string_list(action.get("blockedReasonCodes")),
    }


def _summary_text(
    candidate_id: str,
    item: Mapping[str, Any],
    select_action: Mapping[str, Any] | None,
    reject_action: Mapping[str, Any] | None,
) -> str:
    action_text = _optional_text(
        select_action.get("text") if select_action is not None else None
    ) or _optional_text(reject_action.get("text") if reject_action is not None else None)
    if action_text:
        return action_text
    label = _optional_text(item.get("label"))
    score_text = _score_text(item.get("score"))
    if label and score_text:
        return f"{label} {score_text}"
    return label or candidate_id


def _score_text(value: object) -> str | None:
    numeric = _optional_float(value)
    if numeric is None:
        return None
    return f"{round(max(0.0, min(1.0, numeric)) * 100)}%"


def _style_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    payload: dict[str, Any] = {}
    if "colorMode" in value:
        payload["colorMode"] = _optional_text(value.get("colorMode"))
    if "fillRgba" in value:
        payload["fillRgba"] = _int_list(value.get("fillRgba"))
    if "outlineRgba" in value:
        payload["outlineRgba"] = _int_list(value.get("outlineRgba"))
    if "opacity" in value:
        payload["opacity"] = _optional_float(value.get("opacity"))
    return {key: payload[key] for key in payload if key in _STYLE_KEYS}


def _mapping_items(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
        return []
    result: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            result.append(text)
    return result


def _number_list(value: object) -> list[int | float]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
        return []
    result: list[int | float] = []
    for item in value:
        number = _optional_number(item)
        if number is not None:
            result.append(number)
    return result


def _optional_number_list(value: object) -> list[int | float] | None:
    if value is None:
        return None
    return _number_list(value)


def _int_list(value: object) -> list[int]:
    return [int(number) for number in _number_list(value)]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _optional_int(value: object) -> int | None:
    number = _optional_number(value)
    if number is None:
        return None
    return int(number)


def _optional_float(value: object) -> float | None:
    number = _optional_number(value)
    if number is None:
        return None
    return float(number)


def _optional_number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric
