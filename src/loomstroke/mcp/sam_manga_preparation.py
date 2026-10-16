"""Local manga acceptance, protection masks and protected inpaint preparation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from loomstroke.ai_selection.generation_mask_qa import analyze_generation_mask_asset
from loomstroke.ai_selection.manga_inpaint_prepare import build_manga_inpaint_preparation
from loomstroke.ai_selection.manga_mask_categories import normalize_manga_mask_category
from loomstroke.ai_selection.manga_mask_package import (
    build_comic_mask_package_from_candidate_set,
    comic_mask_candidate_ref_from_candidate,
)
from loomstroke.ai_selection.mask_combine_adapter import combine_mask_assets
from loomstroke.ai_selection.speech_bubble_protect import (
    build_speech_bubble_protect_plan,
    combine_speech_bubble_protect_mask_assets,
    save_speech_bubble_protect_mask_asset,
)

from .editing_common import edit_operation, invalid
from .sam_asset_common import (
    MAX_AGGREGATE_MASK_PIXELS,
    asset_error,
    dimensions,
    identifier,
    load,
    mask_result,
    output_id,
    provider_store,
)
from .types import McpStructuredError, ReasonCode


def _category(args: Mapping[str, Any]) -> Any:
    value = normalize_manga_mask_category(args.get("category"))
    if value is None:
        raise invalid("'category' must name a registered manga mask category.", "category")
    return value


async def prepare_text_protection(
    service: Any, name: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    _provider, store = provider_store(service)
    candidates = load(store, "candidate_set", args.get("candidateSetId"), "candidateSetId")
    accepted = args.get("acceptedCandidateIds")
    if (
        not isinstance(accepted, list)
        or len(accepted) > 32
        or any(not isinstance(value, str) for value in accepted)
        or len(set(accepted)) != len(accepted)
    ):
        raise invalid(
            "'acceptedCandidateIds' must contain at most 32 distinct IDs.", "acceptedCandidateIds"
        )
    by_id = {item.candidate_id: item for item in candidates.candidates}
    if any(identifier(value, "acceptedCandidateIds") not in by_id for value in accepted):
        raise McpStructuredError(
            ReasonCode.NOT_FOUND, "An accepted candidate is not in the saved candidate set."
        )
    category_map = args.get("categoryByCandidateId", {})
    if (
        not isinstance(category_map, Mapping)
        or len(category_map) > 32
        or any(
            key not in by_id or normalize_manga_mask_category(value) is None
            for key, value in category_map.items()
        )
    ):
        raise invalid(
            "'categoryByCandidateId' must map saved candidate IDs to manga categories.",
            "categoryByCandidateId",
        )
    package = build_comic_mask_package_from_candidate_set(
        candidates,
        _category(args),
        accepted_candidate_ids=accepted,
        category_by_candidate_id=category_map,
    )
    plan = build_speech_bubble_protect_plan(package, require_accepted=True)
    if not plan.ready:
        return {
            "status": "blocked",
            "blockedReasonCodes": list(plan.blocked_reason_codes),
            "package": package.to_dict(),
            "canvasChanged": False,
        }
    width, height = dimensions(service, args)
    if width * height * len(plan.mask_asset_ids) > MAX_AGGREGATE_MASK_PIXELS:
        raise invalid(
            "Text protection exceeds the 64 MP aggregate mask limit.", "acceptedCandidateIds"
        )
    async with edit_operation(service, name, args, require_canvas=False):
        asset_id = output_id(store, args.get("outputAssetId"))
        try:
            combined = await asyncio.to_thread(
                combine_speech_bubble_protect_mask_assets,
                store,
                plan,
                canvas_width=width,
                canvas_height=height,
            )
            if not combined.ready:
                return {
                    "status": "blocked",
                    "blockedReasonCodes": list(combined.blocked_reason_codes),
                    "canvasChanged": False,
                }
            asset = await asyncio.to_thread(
                save_speech_bubble_protect_mask_asset,
                store,
                combined,
                output_asset_id=asset_id,
                canvas_width=width,
                canvas_height=height,
                metadata={"acceptedCandidateIds": accepted, "comicMaskPackage": package.to_dict()},
            )
        except (ValueError, OSError) as exc:
            raise asset_error(exc) from exc
    return {**mask_result(asset), "package": package.to_dict(), "protectionPlan": plan.to_dict()}


async def prepare_inpaint(service: Any, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    _provider, store = provider_store(service)
    candidates = load(store, "candidate_set", args.get("candidateSetId"), "candidateSetId")
    candidate_id = identifier(args.get("candidateId"), "candidateId")
    candidate = next(
        (item for item in candidates.candidates if item.candidate_id == candidate_id), None
    )
    if candidate is None:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "The candidate is not in the saved set.")
    accepted = args.get("accepted", False)
    if not isinstance(accepted, bool):
        raise invalid("'accepted' must be boolean.", "accepted")
    protected = args.get("protectedMaskAssetId")
    if protected is not None:
        protected = load(store, "mask_asset", protected, "protectedMaskAssetId").asset_id
    ref = comic_mask_candidate_ref_from_candidate(candidate, _category(args), accepted=accepted)
    preparation = build_manga_inpaint_preparation(ref, protected_mask_asset_id=protected)
    if not preparation.ready:
        return {"status": "blocked", "preparation": preparation.to_dict(), "canvasChanged": False}
    width, height = dimensions(service, args)
    async with edit_operation(service, name, args, require_canvas=False):
        asset_id = output_id(store, args.get("outputAssetId"))
        try:
            asset = await asyncio.to_thread(
                combine_mask_assets,
                store,
                [candidate.mask_asset_id, protected],
                output_asset_id=asset_id,
                canvas_width=width,
                canvas_height=height,
                mode="subtract",
                source_candidate_id=candidate_id,
                metadata={
                    "accepted": True,
                    "category": ref.category,
                    "protectedMaskAssetId": protected,
                },
            )
            qa = await asyncio.to_thread(
                analyze_generation_mask_asset,
                store,
                asset.asset_id,
                canvas_width=width,
                canvas_height=height,
                protected_mask_asset_id=protected,
            )
        except (ValueError, OSError) as exc:
            raise asset_error(exc) from exc
    preparation = replace(preparation, inpaint_mask_asset_id=asset.asset_id)
    return {**mask_result(asset), "preparation": preparation.to_dict(), "qa": qa.to_dict()}


async def protection_qa(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    _provider, store = provider_store(service)
    asset = load(store, "mask_asset", args.get("maskAssetId"), "maskAssetId")
    protected = args.get("protectedMaskAssetId")
    if protected is not None:
        protected = load(store, "mask_asset", protected, "protectedMaskAssetId").asset_id
    width, height = dimensions(service, args)
    try:
        qa = await asyncio.to_thread(
            analyze_generation_mask_asset,
            store,
            asset.asset_id,
            canvas_width=width,
            canvas_height=height,
            protected_mask_asset_id=protected,
        )
    except (ValueError, OSError) as exc:
        raise asset_error(exc) from exc
    return {"status": "completed", "qa": qa.to_dict(), "canvasChanged": False}
