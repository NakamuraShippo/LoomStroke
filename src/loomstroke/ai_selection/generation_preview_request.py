"""Preview-only AI Effect generation request helpers for SAM3 masks."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from .generation_mask_qa import Sam3GenerationMaskQA
from .metadata_scrub import scrub_sam3_metadata

SAM3_AI_EFFECT_PREVIEW_REQUEST_SCHEMA_VERSION = "sam3.ai_effect_preview_request.v1"

_METADATA_SOURCE = "sam3.ai_effect.preview_generation"
_VALID_TRIGGERS = {"manual", "on_change", "debounced"}
_VALID_OUTPUT_OVERRIDES = {"self", "new_layer_above"}


class Sam3AIEffectPreviewRequestError(ValueError):
    """Raised when a SAM3 AI Effect preview request cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_ai_effect_preview_generation_request(  # noqa: PLR0913
    layer_id: str,
    *,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    mask_asset_id: str | None = None,
    qa: Sam3GenerationMaskQA | Mapping[str, object] | None = None,
    force_regenerate: bool = False,
    param_values: Mapping[str, object] | None = None,
    slot_refs: Mapping[str, object] | None = None,
    param_ui_state: Mapping[str, object] | None = None,
    replace_param_values: bool = False,
    replace_slot_refs: bool = False,
    replace_param_ui_state: bool = False,
    trigger: str | None = None,
    target_server_id: str | None = None,
    output_mode_override: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build ``loomstroke_submit_generation`` args for preview-only SAM3 generation."""

    clean_layer_id = _required_text(
        layer_id,
        code="ai_effect_layer_id_missing",
        message="SAM3 preview generation requires an AI Effect layer id.",
    )
    request: dict[str, object] = {
        "layerId": clean_layer_id,
        "forceRegenerate": bool(force_regenerate),
        "persistOverrides": False,
        "applyToCanvas": False,
        "metadata": _preview_metadata(
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            mask_asset_id=mask_asset_id,
            qa=qa,
            metadata=metadata,
        ),
    }

    _copy_mapping_arg(request, "paramValues", param_values)
    _copy_mapping_arg(request, "slotRefs", slot_refs)
    _copy_mapping_arg(request, "paramUiState", param_ui_state)
    if replace_param_values:
        request["replaceParamValues"] = True
    if replace_slot_refs:
        request["replaceSlotRefs"] = True
    if replace_param_ui_state:
        request["replaceParamUiState"] = True
    if trigger is not None:
        clean_trigger = _required_text(
            trigger,
            code="ai_effect_preview_trigger_invalid",
            message="SAM3 preview generation trigger must be manual, on_change, or debounced.",
        )
        if clean_trigger not in _VALID_TRIGGERS:
            raise Sam3AIEffectPreviewRequestError(
                "ai_effect_preview_trigger_invalid",
                "SAM3 preview generation trigger must be manual, on_change, or debounced.",
            )
        request["trigger"] = clean_trigger
    clean_target_server_id = _optional_text(target_server_id)
    if clean_target_server_id is not None:
        request["targetServerId"] = clean_target_server_id
    clean_output_mode = _optional_text(output_mode_override)
    if clean_output_mode is not None:
        if clean_output_mode not in _VALID_OUTPUT_OVERRIDES:
            raise Sam3AIEffectPreviewRequestError(
                "ai_effect_preview_output_mode_invalid",
                "SAM3 preview generation output mode must be self or new_layer_above.",
            )
        request["outputModeOverride"] = clean_output_mode
    return request


def _preview_metadata(
    *,
    candidate_set_id: str | None,
    candidate_id: str | None,
    mask_asset_id: str | None,
    qa: Sam3GenerationMaskQA | Mapping[str, object] | None,
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    if metadata is not None and not isinstance(metadata, Mapping):
        raise Sam3AIEffectPreviewRequestError(
            "ai_effect_preview_metadata_invalid",
            "SAM3 preview generation metadata must be a mapping.",
        )
    preview_payload: dict[str, object] = {
        "schemaVersion": SAM3_AI_EFFECT_PREVIEW_REQUEST_SCHEMA_VERSION,
    }
    for key, value in (
        ("candidateSetId", candidate_set_id),
        ("candidateId", candidate_id),
        ("maskAssetId", mask_asset_id),
    ):
        clean_value = _optional_text(value)
        if clean_value is not None:
            preview_payload[key] = clean_value
    qa_payload = _qa_payload(qa)
    if qa_payload is not None:
        preview_payload["qa"] = qa_payload

    safe_metadata = dict(scrub_sam3_metadata(dict(metadata or {})))
    safe_metadata.update(
        {
            "source": _METADATA_SOURCE,
            "candidateRequest": True,
            "candidateCount": 1,
            "sam3Preview": scrub_sam3_metadata(preview_payload),
        }
    )
    return safe_metadata


def _qa_payload(
    qa: Sam3GenerationMaskQA | Mapping[str, object] | None,
) -> dict[str, object] | None:
    if qa is None:
        return None
    if isinstance(qa, Sam3GenerationMaskQA):
        if not qa.ok:
            raise Sam3AIEffectPreviewRequestError(
                "generation_mask_qa_failed",
                "SAM3 preview generation mask QA did not pass.",
            )
        return dict(scrub_sam3_metadata(qa.to_dict()))
    if isinstance(qa, Mapping):
        payload = dict(scrub_sam3_metadata(dict(qa)))
        if payload.get("ok") is False:
            raise Sam3AIEffectPreviewRequestError(
                "generation_mask_qa_failed",
                "SAM3 preview generation mask QA did not pass.",
            )
        return payload
    raise Sam3AIEffectPreviewRequestError(
        "generation_mask_qa_invalid",
        "SAM3 preview generation mask QA must be a QA object or mapping.",
    )


def _copy_mapping_arg(
    request: dict[str, object],
    key: str,
    value: Mapping[str, object] | None,
) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise Sam3AIEffectPreviewRequestError(
            "ai_effect_preview_mapping_invalid",
            f"SAM3 preview generation {key} must be a mapping.",
        )
    request[key] = copy.deepcopy(dict(value))


def _required_text(value: object, *, code: str, message: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3AIEffectPreviewRequestError(code, message)
    return text


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "SAM3_AI_EFFECT_PREVIEW_REQUEST_SCHEMA_VERSION",
    "Sam3AIEffectPreviewRequestError",
    "build_ai_effect_preview_generation_request",
]
