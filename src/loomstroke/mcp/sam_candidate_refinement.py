"""Refine a persisted candidate through the existing provider and continuity recovery."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from loomstroke.ai_selection.data_model import Sam3RefineRequest
from loomstroke.ai_selection.worker_client import AiSelectionWorkerUnavailable

from .editing_common import edit_operation, finite_number, invalid
from .sam3_prompt_arguments import parse_sam3_prompt_arguments
from .sam_asset_common import asset_error, identifier, load, provider_store
from .types import McpStructuredError, ReasonCode


async def refine_candidate(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    provider, store = provider_store(service)
    candidate_set = load(store, "candidate_set", arguments.get("candidateSetId"), "candidateSetId")
    candidate_id = identifier(arguments.get("candidateId"), "candidateId")
    candidate = next(
        (item for item in candidate_set.candidates if item.candidate_id == candidate_id), None
    )
    if candidate is None:
        raise McpStructuredError(
            ReasonCode.NOT_FOUND, "The candidate is not in the saved candidate set."
        )
    load(store, "mask_asset", candidate.mask_asset_id, "maskAssetId")
    points = arguments.get("points")
    if not isinstance(points, list) or not 1 <= len(points) <= 64:
        raise invalid("'points' must contain 1-64 positive/negative source-space points.", "points")
    for index, point in enumerate(points):
        if not isinstance(point, Mapping) or set(point) - {"x", "y", "label"}:
            raise invalid("Refine points accept x, y and label only.", "points")
        x = finite_number(point.get("x"), f"points[{index}].x", minimum=0)
        y = finite_number(point.get("y"), f"points[{index}].y", minimum=0)
        if x >= candidate_set.source_ref.width or y >= candidate_set.source_ref.height:
            raise invalid("Refine points must be inside the saved source image.", "points")
    prompt = parse_sam3_prompt_arguments(
        {
            "prompt": {
                "mode": "point",
                "text": candidate.prompt.normalized_text or candidate.prompt.text,
                "points": points,
            }
        }
    )
    request = Sam3RefineRequest(
        request_id=uuid.uuid4().hex,
        source_ref=candidate_set.source_ref,
        base_mask_asset_id=candidate.mask_asset_id,
        prompt=prompt,
    )
    async with edit_operation(service, name, arguments, require_canvas=False):
        try:
            result = await provider.refine(request)
        except AiSelectionWorkerUnavailable as exc:
            reasons = (
                [reason.to_dict() for reason in exc.readiness.blocked_reasons]
                if exc.readiness
                else []
            )
            return {"status": "blocked", "blockedReasons": reasons, "canvasChanged": False}
        except (OSError, ValueError) as exc:
            raise asset_error(exc) from exc
    return {
        "status": "completed",
        "candidateSetId": result.set_id,
        "jobId": result.job_id,
        "candidateCount": len(result.candidates),
        "resourceUri": f"loomstroke://sam3/candidate-set/{result.set_id}",
        "canvasChanged": False,
    }
