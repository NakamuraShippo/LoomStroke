"""Runtime-only generation candidate stack helpers for Canvas Agent."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .canvas_visual_preview import strip_canvas_agent_payloads
from .safety import scrub_secrets
from .types import McpStructuredError, ReasonCode

CANVAS_CANDIDATE_STACK_SCHEMA_VERSION = "canvas_candidate_stack.v1"
CHANGESET_SCHEMA_VERSION = "canvas_changeset.v1"
VALIDATION_SCHEMA_VERSION = "canvas_changeset_validation.v1"


def build_canvas_candidate_stack(
    *,
    run_id: str | None,
    job: Mapping[str, Any],
    assets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a payload-free candidate stack from registered generation assets."""

    job_id = str(job.get("jobId") or "").strip()
    candidates: list[dict[str, Any]] = []
    for index, asset in enumerate(assets, start=1):
        asset_id = str(asset.get("assetId") or "").strip()
        if not asset_id:
            continue
        dimensions = _dimensions(asset.get("dimensions"))
        metadata = _mapping(asset.get("metadata"))
        candidate_id = str(
            metadata.get("candidateId") or metadata.get("candidateIndex") or f"cand_{index}"
        ).strip()
        if candidate_id.isdigit():
            candidate_id = f"cand_{candidate_id}"
        if not candidate_id.startswith("cand_"):
            candidate_id = f"cand_{candidate_id}"
        source = str(asset.get("source") or metadata.get("provider") or "generation")
        thumbnail_asset_id = str(metadata.get("thumbnailAssetId") or asset_id).strip()
        candidates.append(
            {
                "candidateId": candidate_id,
                "assetId": asset_id,
                "thumbnailAssetId": thumbnail_asset_id,
                "label": str(metadata.get("label") or f"案 {len(candidates) + 1}"),
                "targetLayerHint": "new_layer",
                "recommendedOpacity": _float(metadata.get("recommendedOpacity"), 1.0),
                "recommendedBlendMode": str(metadata.get("recommendedBlendMode") or "normal"),
                "provider": source,
                "size": dimensions,
                "warnings": _warnings(metadata.get("warnings")),
            }
        )

    return scrub_secrets(
        strip_canvas_agent_payloads(
            {
                "schemaVersion": CANVAS_CANDIDATE_STACK_SCHEMA_VERSION,
                "stackId": f"cand_stack_{uuid.uuid4().hex[:12]}",
                "runId": run_id,
                "source": "generation_job",
                "jobId": job_id,
                "candidates": candidates,
                "selectedCandidateId": None,
                "privacy": {
                    "externalGenerationUsed": True,
                    "payloadOmitted": True,
                },
            }
        )
    )


def with_selected_candidate(
    stack: Mapping[str, Any],
    selected_candidate_id: str,
) -> dict[str, Any]:
    """Return a stack copy with a selected candidate id."""

    candidate = selected_candidate(stack, selected_candidate_id)
    selected = str(candidate.get("candidateId") or "").strip()
    payload = dict(stack)
    payload["selectedCandidateId"] = selected
    return scrub_secrets(strip_canvas_agent_payloads(payload))


def selected_candidate(
    stack: Mapping[str, Any],
    selected_candidate_id: str | None = None,
) -> Mapping[str, Any]:
    """Return the selected candidate, or raise a structured error."""

    if str(stack.get("schemaVersion") or "") != CANVAS_CANDIDATE_STACK_SCHEMA_VERSION:
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'candidateStack' must use canvas_candidate_stack.v1.",
            details={"schemaVersion": stack.get("schemaVersion")},
            code=-32602,
        )
    selected = str(selected_candidate_id or stack.get("selectedCandidateId") or "").strip()
    if not selected:
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "A generation candidate must be selected before preview or apply.",
            details={"stackId": stack.get("stackId"), "code": "candidate_selection_required"},
        )
    for candidate in _candidates(stack):
        if str(candidate.get("candidateId") or "").strip() == selected:
            if not str(candidate.get("assetId") or "").strip():
                raise McpStructuredError(
                    ReasonCode.CONFLICT,
                    "Selected generation candidate does not reference an asset.",
                    details={
                        "stackId": stack.get("stackId"),
                        "candidateId": selected,
                        "code": "candidate_asset_required",
                    },
                )
            return candidate
    raise McpStructuredError(
        ReasonCode.NOT_FOUND,
        "Selected generation candidate was not found in the candidate stack.",
        details={
            "stackId": stack.get("stackId"),
            "selectedCandidateId": selected,
            "code": "candidate_not_found",
        },
    )


def build_selected_candidate_changeset(
    *,
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    candidate_stack: Mapping[str, Any],
    selected_candidate_id: str | None = None,
) -> dict[str, Any]:
    """Build a CanvasChangeset that imports exactly one selected candidate."""

    candidate = selected_candidate(candidate_stack, selected_candidate_id)
    now = datetime.now(UTC).isoformat()
    changeset_id = f"chg_{uuid.uuid4().hex[:12]}"
    prefix = changeset_id.removeprefix("chg_")
    target = _target_from_plan_or_context(plan, context)
    candidate_id = str(candidate.get("candidateId") or "").strip()
    asset_id = str(candidate.get("assetId") or "").strip()
    opacity = _float(candidate.get("recommendedOpacity"), 1.0)
    blend_mode = str(candidate.get("recommendedBlendMode") or "normal")
    label = str(candidate.get("label") or "Generated Candidate")
    changeset = {
        "schemaVersion": CHANGESET_SCHEMA_VERSION,
        "changesetId": changeset_id,
        "sourcePlanId": plan.get("planId"),
        "runId": plan.get("runId") or candidate_stack.get("runId"),
        "contextId": plan.get("contextId") or context.get("contextId"),
        "baseDocumentRevision": plan.get("contextDigest") or context.get("contextDigest"),
        "createdAt": now,
        "updatedAt": now,
        "status": "draft",
        "target": target,
        "operations": [
            {
                "operationId": f"op_{prefix}_candidate_import",
                "op": "submit_generation_to_new_layer",
                "assetId": asset_id,
                "newLayerId": f"layer_{prefix}_candidate",
                "candidateId": candidate_id,
                "name": label,
                "opacity": opacity,
                "blendMode": blend_mode,
            }
        ],
        "candidateStack": {
            "stackId": candidate_stack.get("stackId"),
            "selectedCandidateId": candidate_id,
            "jobId": candidate_stack.get("jobId"),
            "source": candidate_stack.get("source"),
        },
        "blockedReasons": [],
        "validation": {
            "schemaVersion": VALIDATION_SCHEMA_VERSION,
            "status": "not_validated",
            "errors": [],
            "warnings": [],
        },
        "preview": {"status": "not_created", "previewId": None},
        "approval": {"required": True, "ticketId": None, "status": "not_issued"},
        "safety": {
            "rawPromptIncluded": False,
            "rawBinaryIncluded": False,
            "credentialReadPerformed": False,
            "externalModelCalled": False,
            "externalGenerationStartedInApply": False,
            "candidatePayloadOmitted": True,
        },
        "sourcePromptDigest": plan.get("sourcePromptDigest"),
    }
    return scrub_secrets(strip_canvas_agent_payloads(changeset))


def _target_from_plan_or_context(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    target = _mapping(plan.get("target"))
    document = _mapping(context.get("document"))
    page = _mapping(context.get("page"))
    active = _mapping(context.get("activeLayer"))
    selection = _mapping(context.get("selection"))
    return {
        "documentId": target.get("documentId") or document.get("documentId"),
        "pageId": target.get("pageId") or page.get("pageId"),
        "layerId": target.get("layerId") or active.get("layerId"),
        "hasSelection": bool(target.get("hasSelection") or selection.get("hasSelection")),
    }


def _candidates(stack: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates = stack.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        return []
    return [candidate for candidate in candidates if isinstance(candidate, Mapping)]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _dimensions(value: Any) -> dict[str, int]:
    dimensions = _mapping(value)
    return {
        "width": max(0, _int(dimensions.get("width"), 0)),
        "height": max(0, _int(dimensions.get("height"), 0)),
    }


def _warnings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return ["metadata_warnings_omitted"] if any(str(item or "").strip() for item in value) else []


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


__all__ = [
    "CANVAS_CANDIDATE_STACK_SCHEMA_VERSION",
    "build_canvas_candidate_stack",
    "build_selected_candidate_changeset",
    "selected_candidate",
    "with_selected_candidate",
]
