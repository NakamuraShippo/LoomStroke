"""ProductionRecipe runner for LoomStroke MCP.

The runner is the canonical production orchestration surface.  Existing
Production Control Tower resources can project these records, but recipe
execution, planning, scoring, rework, and package metadata are modeled here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from loomstroke.ai_selection import (
    AiSelectionWorkerUnavailable,
    Sam3ComicMaskCandidateRef,
    Sam3ComicMaskPackage,
    Sam3ComicMaskPackageError,
    Sam3MangaMaskScorecardError,
    Sam3MangaMaskScorecardProjectionError,
    Sam3PanelMaskSet,
    Sam3ProductionRecipeDescriptor,
    Sam3Prompt,
    Sam3SegmentRequest,
    build_manga_mask_scorecard_production_projection,
    categories_for_manga_mask_preset,
    normalize_manga_mask_category,
    sam3_production_recipe_descriptors,
    score_comic_mask_package_candidates,
)

from . import production as pct
from .generation_capabilities import production_generation_blocks
from .production_generation_preflight import preflight_generation
from .types import JobRecord, McpStructuredError, ReasonCode

RUNNER_SCHEMA_VERSION = "production.recipe.runner.v1"

_DOMAINS = frozenset(
    {
        "comic_page",
        "character_design",
        "character_sheet",
        "game_asset_3d",
        "texture_set",
        "sprite_sheet",
        "background_art",
        "item_icon",
    }
)

_ARTIFACT_KINDS = (
    "bitmap_image",
    "layered_document",
    "comic_page",
    "comic_panel",
    "comic_package",
    "character_design",
    "character_sheet",
    "sprite_sheet",
    "texture_set",
    "material",
    "mesh_3d",
    "rig_3d",
    "animation_clip",
    "game_asset_package",
    "asset_package",
    "qa_report",
    "scorecard",
    "lineage",
    "recipe",
)

_SAM3_PACKAGE_MANIFEST_SCHEMA_VERSION = "sam3.production_package_manifest.v1"
_SAM3_LINEAGE_PROJECTION_SCHEMA_VERSION = "sam3.production_lineage_projection.v1"
_SAM3_VISUAL_QA_SCHEMA_VERSION = "sam3.production_visual_qa_projection.v1"
_SAM3_PRODUCTION_CONTROL_SCHEMA_VERSION = "sam3.production_control_projection.v1"
_SAM3_PRODUCTION_JOB_CANDIDATES_SCHEMA_VERSION = "sam3.production_job_candidates.v1"
_SAM3_PRODUCTION_ARTIFACT_MASKS_SCHEMA_VERSION = "sam3.production_artifact_masks.v1"

# Explicit allow-list of SAM3 recipes wired for submit (Phase 2: all four).
# Kept as an enumeration (not frozenset(SAM3_PRODUCTION_RECIPE_IDS)) so a
# future recipe stays dry_run-only until its default prompts and tests land.
# Public so read-only projections (PCT panel) can mirror the actual modes.
SAM3_SUBMITTABLE_RECIPE_IDS = frozenset(
    {
        "sam3_extract_masks",
        "sam3_split_character_layers",
        "sam3_prepare_inpaint_package",
        "sam3_detect_speech_bubbles",
    }
)
_SAM3_DEFAULT_PROMPTS = ("character", "background", "speech bubble")
_SAM3_MAX_PROMPTS = 8


def _sam3_preset_prompts(preset: str) -> tuple[str, ...]:
    # Space form keeps the text prompt natural for the real model while
    # normalize_manga_mask_category treats it the same as the canonical value.
    return tuple(
        category.replace("_", " ") for category in categories_for_manga_mask_preset(preset)
    )


_SAM3_RECIPE_DEFAULT_PROMPTS: dict[str, tuple[str, ...]] = {
    "sam3_extract_masks": _SAM3_DEFAULT_PROMPTS,
    "sam3_split_character_layers": _sam3_preset_prompts("character_extraction"),
    "sam3_prepare_inpaint_package": _sam3_preset_prompts("inpaint_prep"),
    "sam3_detect_speech_bubbles": _sam3_preset_prompts("speech_bubble_protection"),
}


def _sam3_default_prompts(recipe_id: str) -> tuple[str, ...]:
    return _SAM3_RECIPE_DEFAULT_PROMPTS.get(recipe_id, _SAM3_DEFAULT_PROMPTS)


_SAM3_EXPORT_PROFILE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "sam3_mask_package": (
        "mask_package_json",
        "mask_preview_png",
        "candidate_scorecard_json",
        "qa_report_json",
        "lineage_json",
    ),
    "sam3_layered_character_package": (
        "layered_character_package_json",
        "character_mask_png",
        "background_mask_png",
        "candidate_scorecard_json",
        "lineage_json",
    ),
    "sam3_inpaint_package": (
        "inpaint_package_json",
        "inpaint_mask_png",
        "protect_mask_json",
        "qa_report_json",
        "lineage_json",
    ),
}


def list_production_recipes(
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    args = dict(arguments or {})
    domain = str(args.get("domain") or "all")
    include_experimental = bool(args.get("includeExperimental", False))
    recipes = [
        recipe
        for recipe in _recipes()
        if (domain == "all" or recipe["domain"] == domain)
        and (include_experimental or not recipe.get("experimental"))
    ]
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "canonicalRunner": "loomstroke_run_production_recipe",
        "productionControlTowerRole": "read_only_projection",
        "recipes": recipes,
    }


def get_production_recipe_detail(recipe_id: str) -> dict[str, Any]:
    recipe = _recipe_by_id(recipe_id)
    if recipe is None:
        return _error("ERR_RECIPE_NOT_FOUND", f"ProductionRecipe not found: {recipe_id}")
    detail = dict(recipe)
    detail["inputSchema"] = _input_schema_for_domain(str(recipe["domain"]))
    detail["steps"] = _steps_for_recipe(str(recipe["recipeId"]), str(recipe["domain"]))
    detail["sideEffects"] = _side_effects_for_recipe(str(recipe["recipeId"]))
    sam3_descriptor = _sam3_recipe_descriptor_by_id(str(recipe["recipeId"]))
    if sam3_descriptor is not None:
        detail["requiresSam3Worker"] = sam3_descriptor.requires_sam3_worker
        detail["estimatedArtifacts"] = list(sam3_descriptor.estimated_artifacts)
    detail["privacyRequirements"] = {
        "allowExternalServicesRequired": bool(recipe["requiresExternalServices"]),
        "requiresConfirmation": True,
        "credentialValuesIncluded": False,
    }
    detail["artifactKinds"] = list(_ARTIFACT_KINDS)
    return {"schemaVersion": RUNNER_SCHEMA_VERSION, "recipe": detail}


def validate_production_recipe_input(arguments: Mapping[str, Any]) -> dict[str, Any]:
    recipe_id = _required_string(arguments, "recipeId")
    recipe = _recipe_by_id(recipe_id)
    if recipe is None:
        return _validation_result(
            False,
            errors=[_issue("ERR_RECIPE_NOT_FOUND", f"ProductionRecipe not found: {recipe_id}")],
        )
    raw_input = arguments.get("input")
    if not isinstance(raw_input, Mapping):
        return _validation_result(
            False,
            errors=[_issue("ERR_INVALID_RECIPE_INPUT", "'input' must be an object.")],
        )

    normalized, warnings, errors = _normalize_input(recipe, raw_input)
    return _validation_result(
        not errors,
        warnings=warnings,
        errors=errors,
        normalized_input=normalized,
    )


def plan_production_job(service: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    recipe_id = _required_string(arguments, "recipeId")
    validation = validate_production_recipe_input(arguments)
    if not validation["valid"]:
        return {
            "schemaVersion": RUNNER_SCHEMA_VERSION,
            "status": "invalid",
            "recipeId": recipe_id,
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        }
    recipe = _recipe_by_id(recipe_id)
    assert recipe is not None
    normalized = validation["normalizedInput"]
    plan_id = _stable_id("plan", {"recipeId": recipe_id, "input": normalized})
    side_effects = _side_effects_for_recipe(recipe_id)
    requires_external = bool(recipe["requiresExternalServices"])
    file_write = "file_write" in side_effects
    live_canvas_mutation = "live_canvas_mutation" in side_effects
    capability_check = _capability_check(
        service,
        requires_external,
        file_write,
        sam3_recipe=_sam3_recipe_descriptor_by_id(recipe_id) is not None,
    )
    blocked = list(capability_check["missing"])
    generation_validation = {"valid": True, "observed": False}
    if requires_external and _sam3_recipe_descriptor_by_id(recipe_id) is None:
        generation_validation = preflight_generation(service, normalized)
        if not generation_validation["valid"]:
            blocked.append("production_generation_input")
    status = "ready_for_confirmation" if not blocked else "blocked"
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "status": status,
        "recipeId": recipe_id,
        "domain": recipe["domain"],
        "planId": plan_id,
        "requires": {
            "externalServices": requires_external,
            "credentials": _credentials_for_recipe(recipe_id) if requires_external else [],
            "confirmation": True,
            "fileWrite": file_write,
            "liveCanvasMutation": live_canvas_mutation,
        },
        "capabilityCheck": capability_check,
        "steps": _planned_steps(recipe_id, str(recipe["domain"]), normalized),
        "estimatedArtifacts": _estimated_artifacts_for_recipe(
            recipe_id,
            str(recipe["domain"]),
        ),
        "sideEffects": side_effects,
        "warnings": validation["warnings"],
        "blockedReasons": blocked,
        "generationValidation": generation_validation,
        "normalizedInput": normalized,
        "confirmationPayload": {
            "planId": plan_id,
            "allowedSideEffects": side_effects,
        },
    }


async def run_production_recipe(  # noqa: PLR0911
    service: Any,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    recipe_id = _required_string(arguments, "recipeId")
    mode = str(arguments.get("mode") or "dry_run")
    if mode == "dry_run":
        return plan_production_job(service, arguments)
    if mode != "submit":
        return _error("ERR_INVALID_RECIPE_INPUT", "mode must be 'dry_run' or 'submit'.")

    plan = plan_production_job(service, arguments)
    if plan.get("status") != "ready_for_confirmation":
        return plan
    if (
        _sam3_recipe_descriptor_by_id(recipe_id) is not None
        and recipe_id not in SAM3_SUBMITTABLE_RECIPE_IDS
    ):
        return _error(
            "ERR_RECIPE_NOT_IMPLEMENTED",
            "SAM3 ProductionRecipe submit is not connected yet; use dry_run planning.",
            details={"recipeId": recipe_id, "supportedModes": ["dry_run"]},
        )
    confirmation = arguments.get("confirmation")
    if not isinstance(confirmation, Mapping):
        return _error(
            "ERR_CONFIRMATION_REQUIRED",
            "ProductionRecipe submit requires confirmation.",
            details={"requiredConfirmation": plan["requires"]},
        )
    if str(confirmation.get("planId") or "") != str(plan["planId"]):
        return _error(
            "ERR_CONFIRMATION_REQUIRED",
            "ProductionRecipe submit confirmation must reference the dry-run planId.",
            details={"planId": plan["planId"]},
        )
    missing_confirmation = _missing_confirmation(plan, confirmation)
    if missing_confirmation:
        return _error(
            "ERR_CONFIRMATION_REQUIRED",
            "ProductionRecipe submit confirmation is incomplete.",
            details={"missing": missing_confirmation, "requiredConfirmation": plan["requires"]},
        )
    await service.safety.require_confirmation(
        "loomstroke_run_production_recipe",
        _runner_confirmation_details(plan, confirmation),
    )

    # Confirmation can yield while privacy, providers or presets are changed.
    fresh_plan = plan_production_job(service, arguments)
    if fresh_plan.get("status") != "ready_for_confirmation":
        return fresh_plan
    plan = fresh_plan

    now = _now_iso()
    job_id = _stable_id(
        "job_prod",
        {"planId": plan["planId"], "createdAt": now, "recipeId": plan["recipeId"]},
    )
    job = {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "jobId": job_id,
        "recipeId": plan["recipeId"],
        "domain": plan["domain"],
        "status": "queued",
        "input": pct.scrub_for_pct(dict(arguments.get("input") or {})),
        "normalizedInput": plan["normalizedInput"],
        "qualityProfile": plan["normalizedInput"].get("qualityProfile"),
        "exportProfile": plan["normalizedInput"].get("exportProfile"),
        "candidatePolicy": plan["normalizedInput"].get("candidatePolicy"),
        "createdAt": now,
        "updatedAt": now,
        "steps": plan["steps"],
        "artifacts": [],
        "lineageId": _stable_id("lineage", {"jobId": job_id}),
        "source": "production_recipe_runner",
        "plan": plan,
    }
    service._production_store.add_job(job)  # noqa: SLF001
    mcp_job = JobRecord(
        job_id=job_id,
        job_class="production_recipe",
        status="queued",
        progress=0.0,
        metadata={
            "source": "production_recipe_runner",
            "recipeId": plan["recipeId"],
            "domain": plan["domain"],
            "planId": plan["planId"],
        },
    )
    service._jobs[job_id] = mcp_job  # noqa: SLF001
    task = asyncio.create_task(_execute_runner_job(service, job_id))
    service._job_tasks[job_id] = task  # noqa: SLF001
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "jobId": job_id,
        "status": "queued",
        "recipeId": plan["recipeId"],
        "resources": {
            "job": f"loomstroke://production/job/{job_id}",
            "plan": f"loomstroke://production/job/{job_id}/plan",
        },
    }


async def _execute_runner_job(service: Any, job_id: str) -> None:  # noqa: PLR0915
    store = service._production_store  # noqa: SLF001
    job = store.get_record("jobs", "jobId", job_id)
    mcp_job = service._jobs.get(job_id)  # noqa: SLF001
    if job is None or mcp_job is None:
        return
    if _sam3_recipe_descriptor_by_id(str(job.get("recipeId") or "")) is not None:
        await _execute_sam3_runner_job(service, store, job, mcp_job)
        return
    try:
        _update_job_status(store, mcp_job, job, "running", progress=0.05)
        steps = [dict(step) for step in job.get("steps", []) if isinstance(step, Mapping)]
        external_results: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        scorecards: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            step_id = str(step.get("stepId") or "")
            _update_job_status(
                store,
                mcp_job,
                job,
                _status_for_step(step_id),
                progress=(index + 1) / max(1, len(steps)),
                current_step=step_id,
            )
            if step_id in {"generate_candidates", "generate_panel_candidates"}:
                submitted = await _submit_generation_candidates(service, job, step)
                external_results.extend(submitted)
                artifacts.extend(_candidate_artifacts(job, step, submitted))
            elif step_id == "import_source_asset" and job.get("domain") == "game_asset_3d":
                artifacts = [_artifact_from_3d_source(service, job, step)]
            elif step_id == "score_candidates":
                scorecard = _scorecard_for_job(job, artifacts)
                scorecards.append(scorecard)
                store.upsert_records("scorecards", [scorecard], key="scorecardId")
            await asyncio.sleep(0)
        if not artifacts:
            artifacts = _placeholder_artifacts(job)
        store.upsert_records("artifacts", artifacts, key="artifactId")
        edges = _lineage_edges_for_job(job, artifacts)
        if edges:
            store.upsert_records("edges", edges, key="edgeId")
        package = _package_record_for_job(job, artifacts)
        store.upsert_records("packages", [package], key="packageId")
        job.update(
            {
                "status": "completed",
                "progress": 1.0,
                "artifacts": artifacts,
                "externalJobs": external_results,
                "scorecards": scorecards,
                "packageId": package["packageId"],
                "updatedAt": _now_iso(),
            }
        )
        store.add_job(job)
        mcp_job.status = "completed"
        mcp_job.progress = 1.0
        mcp_job.result_asset_ids = [
            str(artifact.get("assetId") or "") for artifact in artifacts if artifact.get("assetId")
        ]
        mcp_job.metadata.update(
            {
                "productionStatus": "completed",
                "artifactCount": len(artifacts),
                "externalJobCount": len(external_results),
                "packageId": package["packageId"],
            }
        )
        mcp_job.touch()
    except asyncio.CancelledError:
        _update_job_status(store, mcp_job, job, "cancelled", progress=None)
        raise
    except Exception as exc:  # pragma: no cover - defensive metadata failure path
        job.update(
            {
                "status": "failed",
                "error": {"type": exc.__class__.__name__, "message": str(exc)},
                "updatedAt": _now_iso(),
            }
        )
        store.add_job(job)
        mcp_job.status = "failed"
        mcp_job.progress = None
        mcp_job.metadata["error"] = dict(job["error"])
        mcp_job.touch()


class _Sam3RunnerJobError(RuntimeError):
    """Terminal SAM3 runner job failure with a stable reason code."""

    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        blocked_reason_codes: tuple[str, ...] = (),
    ) -> None:
        self.reason = reason
        self.detail = detail
        self.blocked_reason_codes = blocked_reason_codes
        super().__init__(detail)


async def _execute_sam3_runner_job(
    service: Any,
    store: Any,
    job: dict[str, Any],
    mcp_job: JobRecord,
) -> None:
    """Execute a SAM3 recipe submit job (Phase 1: sam3_extract_masks).

    Every step runs inside this try so a failure can never leave the job
    stuck in running; the job error stores type/reason only (never raw
    exception messages, which may contain absolute paths).
    """

    try:
        _update_job_status(store, mcp_job, job, "running", progress=0.05)
        normalized = dict(job.get("normalizedInput") or {})
        provider = getattr(service, "_sam3_provider", None)
        if provider is None:
            raise _Sam3RunnerJobError("CAPABILITY_UNAVAILABLE", "sam3_provider_missing")
        asset_store = getattr(provider, "asset_store", None)
        if asset_store is None:
            raise _Sam3RunnerJobError("CAPABILITY_UNAVAILABLE", "sam3_asset_store_missing")

        _update_job_status(
            store, mcp_job, job, "running", progress=0.15, current_step="resolve_source_snapshot"
        )
        source_ref = _resolve_sam3_source_snapshot(
            service, asset_store, normalized.get("source"), job_id=str(job["jobId"])
        )

        plan_step = _sam3_plan_step_for_recipe(str(job.get("recipeId") or ""))
        candidate_sets = await _collect_sam3_candidate_sets(
            service, store, mcp_job, job, source_ref, normalized, plan_step=plan_step
        )

        _update_job_status(
            store, mcp_job, job, "scoring", progress=0.75, current_step="score_mask_candidates"
        )
        # The persisted package record id — used everywhere a record refers to
        # the package so artifact/scorecard joins never dangle.
        package_id = _package_id_for_job(job)
        candidate_set_id = _stable_id("sam3_candidates", {"jobId": job["jobId"]})
        candidate_set_payload, candidate_set_ids = _build_sam3_candidate_set_payload(
            normalized, source_ref, candidate_sets, candidate_set_id, package_id
        )
        global_masks = candidate_set_payload["globalMasks"]
        quality_profile = str(normalized.get("qualityProfile") or "")
        score_result = _score_sam3_manga_mask_candidates(
            domain="comic_page",
            quality_profile=quality_profile,
            candidate_set=candidate_set_payload,
        )
        if "error" in score_result:
            raise _Sam3RunnerJobError(
                str(score_result["error"].get("code") or "ERR_INVALID_RECIPE_INPUT"),
                "sam3_scorecard_failed",
            )
        scorecard = {
            "schemaVersion": RUNNER_SCHEMA_VERSION,
            "scorecardId": _stable_id("scorecard", {"jobId": job["jobId"]}),
            "jobId": job["jobId"],
            "domain": job.get("domain"),
            "qualityProfile": quality_profile,
            **{key: value for key, value in score_result.items() if key != "schemaVersion"},
            "createdAt": _now_iso(),
        }
        store.upsert_records("scorecards", [scorecard], key="scorecardId")

        _update_job_status(
            store, mcp_job, job, "running", progress=0.9, current_step="record_lineage"
        )
        artifacts, package = _persist_sam3_job_outputs(
            store,
            job,
            scorecard=scorecard,
            package_id=package_id,
            candidate_set_ids=candidate_set_ids,
            candidate_count=len(global_masks),
            source_snapshot_id=str(source_ref.snapshot_id),
            plan_step=plan_step,
        )

        job.update(
            {
                "status": "completed",
                "progress": 1.0,
                "artifacts": artifacts,
                "scorecards": [scorecard],
                "packageId": package["packageId"],
                "candidateSetIds": candidate_set_ids,
                "updatedAt": _now_iso(),
            }
        )
        store.add_job(job)
        mcp_job.status = "completed"
        mcp_job.progress = 1.0
        mcp_job.metadata.update(
            {
                "productionStatus": "completed",
                "artifactCount": len(artifacts),
                "packageId": package["packageId"],
                "candidateSetIds": list(candidate_set_ids),
            }
        )
        mcp_job.touch()
    except asyncio.CancelledError:
        # "cancelled" (double l) matches the service terminal-status set; the
        # generic executor's "canceled" spelling is a known mismatch we must
        # not replicate here.
        _update_job_status(store, mcp_job, job, "cancelled", progress=None)
        raise
    except _Sam3RunnerJobError as exc:
        _fail_sam3_runner_job(
            store,
            mcp_job,
            job,
            exc.reason,
            exc.detail,
            blocked_reason_codes=exc.blocked_reason_codes,
        )
    except McpStructuredError as exc:
        _fail_sam3_runner_job(store, mcp_job, job, exc.reason_code, type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - job must terminate as failed.
        _fail_sam3_runner_job(store, mcp_job, job, "BACKEND_FAILED", type(exc).__name__)


def _fail_sam3_runner_job(
    store: Any,
    mcp_job: JobRecord,
    job: dict[str, Any],
    reason: str,
    detail: str,
    *,
    blocked_reason_codes: tuple[str, ...] = (),
) -> None:
    error: dict[str, Any] = {"type": detail, "reason": reason}
    if blocked_reason_codes:
        # Stable readiness codes only (never messages/diagnostics): they make
        # a worker_unavailable failure actionable while staying secret-free.
        error["blockedReasonCodes"] = list(blocked_reason_codes)
    job.update(
        {
            "status": "failed",
            "error": error,
            "updatedAt": _now_iso(),
        }
    )
    store.add_job(job)
    mcp_job.status = "failed"
    mcp_job.progress = None
    mcp_job.metadata["error"] = dict(job["error"])
    mcp_job.touch()


def _sam3_blocked_reason_codes(exc: Any) -> tuple[str, ...]:
    """Extract stable blocked-reason codes from a AiSelectionWorkerUnavailable."""

    readiness = getattr(exc, "readiness", None)
    reasons = getattr(readiness, "blocked_reasons", None) or ()
    codes = []
    for reason in reasons:
        code = getattr(reason, "code", None)
        if isinstance(code, str) and code:
            codes.append(code)
    return tuple(codes)


def _sam3_plan_step_for_recipe(recipe_id: str) -> str:
    """Return the recipe's plan_* step id (first match; stable fallback)."""

    for step in _sam3_steps_for_recipe(recipe_id):
        if step.startswith("plan_"):
            return step
    return "plan_mask_candidates"


async def _collect_sam3_candidate_sets(
    service: Any,
    store: Any,
    mcp_job: JobRecord,
    job: dict[str, Any],
    source_ref: Any,
    normalized: Mapping[str, Any],
    *,
    plan_step: str,
) -> list[tuple[str, Any]]:
    """Run one segment request per prompt and collect the candidate sets."""

    prompts = [str(prompt) for prompt in normalized.get("prompts") or []]
    max_candidates = _clamp_int(normalized.get("maxCandidatesPerPrompt"), 1, 64, 16)
    candidate_sets: list[tuple[str, Any]] = []
    for index, prompt in enumerate(prompts):
        _update_job_status(
            store,
            mcp_job,
            job,
            "running",
            progress=0.2 + 0.5 * (index / max(1, len(prompts))),
            current_step=plan_step,
        )
        request = Sam3SegmentRequest(
            request_id=f"{job['jobId']}-p{index}",
            source_ref=source_ref,
            prompt=Sam3Prompt(mode="text", text=prompt),
            max_candidates=max_candidates,
            metadata={"source": "production_recipe_runner", "recipeId": job.get("recipeId")},
        )
        # Re-read per call: the live runtime detaches the provider on stop(),
        # so an in-flight job must fail with the stable capability code
        # instead of an AttributeError on None.
        provider = getattr(service, "sam3_provider", None)
        if provider is None:
            raise _Sam3RunnerJobError("CAPABILITY_UNAVAILABLE", "sam3_provider_missing")
        try:
            candidate_set = await provider.segment(request)
        except AiSelectionWorkerUnavailable as exc:
            raise _Sam3RunnerJobError(
                "CAPABILITY_UNAVAILABLE",
                "sam3_worker_unavailable",
                blocked_reason_codes=_sam3_blocked_reason_codes(exc),
            ) from exc
        candidate_sets.append((prompt, candidate_set))
        await asyncio.sleep(0)
    return candidate_sets


def _build_sam3_candidate_set_payload(
    normalized: Mapping[str, Any],
    source_ref: Any,
    candidate_sets: Sequence[tuple[str, Any]],
    candidate_set_id: str,
    package_id: str,
) -> tuple[dict[str, Any], list[str]]:
    """Flatten per-prompt candidates into one scoring payload.

    Prompt text is mapped to its normalized category here and never stored in
    the payload itself. Worker candidate ids are only unique per request, so
    each global entry is namespaced as "{set_id}:{candidate_id}" (set ids are
    unique per request); the raw id is recoverable after the first colon.
    """

    candidate_set_ids: list[str] = []
    global_masks: list[dict[str, Any]] = []
    seen_candidate_ids: set[str] = set()
    for prompt, candidate_set in candidate_sets:
        candidate_set_ids.append(str(candidate_set.set_id))
        category = normalize_manga_mask_category(prompt)
        if category is None:
            # Guarded at dry_run; defensive for direct store mutation.
            raise _Sam3RunnerJobError("ERR_INVALID_RECIPE_INPUT", "sam3_prompt_category_unknown")
        for candidate in candidate_set.candidates:
            namespaced_id = f"{candidate_set.set_id}:{candidate.candidate_id}"
            if namespaced_id in seen_candidate_ids:
                # A backend integrity failure, not a user-input problem.
                raise _Sam3RunnerJobError("BACKEND_FAILED", "duplicate_candidate_id")
            seen_candidate_ids.add(namespaced_id)
            global_masks.append(
                {
                    "candidateId": namespaced_id,
                    "category": category,
                    "bbox": list(candidate.bbox),
                    "areaPx": candidate.area_px,
                    "score": candidate.score,
                    "accepted": False,
                }
            )
    source_payload = normalized.get("source")
    source_payload = source_payload if isinstance(source_payload, Mapping) else {}
    payload = {
        "kind": "sam3_manga_mask_candidates",
        "candidateSetId": candidate_set_id,
        "packageId": package_id,
        "sourceDocumentId": str(source_payload.get("documentId") or "") or source_ref.snapshot_id,
        "sourcePageId": str(source_payload.get("pageId") or "") or source_ref.snapshot_id,
        "canvas": {"width": source_ref.width, "height": source_ref.height},
        "panelMasks": [],
        "globalMasks": global_masks,
    }
    return payload, candidate_set_ids


def _persist_sam3_job_outputs(
    store: Any,
    job: dict[str, Any],
    *,
    scorecard: Mapping[str, Any],
    package_id: str,
    candidate_set_ids: Sequence[str],
    candidate_count: int,
    source_snapshot_id: str,
    plan_step: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Persist artifacts, lineage edges and the package record for a SAM3 job."""

    descriptor = _sam3_recipe_descriptor_by_id(str(job.get("recipeId") or ""))
    artifact_kinds = tuple(descriptor.estimated_artifacts) if descriptor else ()
    # Prompt text stays out of artifact/scorecard/package records; only
    # ids, counts and normalized categories are persisted.
    artifacts = [
        {
            "schemaVersion": RUNNER_SCHEMA_VERSION,
            "artifactId": _stable_id("artifact", {"jobId": job["jobId"], "kind": artifact_kind}),
            "artifactKind": artifact_kind,
            "status": "ready",
            "createdBy": {
                "jobId": job["jobId"],
                "stepId": plan_step,
                "recipeId": job.get("recipeId"),
            },
            "metadata": {
                "packageId": package_id,
                "candidateSetIds": list(candidate_set_ids),
                "candidateCount": candidate_count,
                "scorecardId": scorecard["scorecardId"],
                "sourceSnapshotId": source_snapshot_id,
            },
            "createdAt": _now_iso(),
        }
        for artifact_kind in artifact_kinds
    ]
    store.upsert_records("artifacts", artifacts, key="artifactId")
    edges = _lineage_edges_for_job(job, artifacts)
    if edges:
        store.upsert_records("edges", edges, key="edgeId")
    package = _package_record_for_job(job, artifacts)
    package["scorecardIds"] = [scorecard["scorecardId"]]
    package["lineageEdgeIds"] = [edge["edgeId"] for edge in edges]
    export_profile = str(job.get("exportProfile") or "")
    manifest = _sam3_package_manifest(job, export_profile, package["artifacts"], [], package)
    if manifest is not None:
        package["packageKind"] = export_profile
        package["sam3PackageManifest"] = manifest
    store.upsert_records("packages", [package], key="packageId")
    return artifacts, package


def _resolve_sam3_source_snapshot(
    service: Any,
    asset_store: Any,
    source: Any,
    *,
    job_id: str,
) -> Any:
    """Resolve the recipe source to a saved source snapshot ref.

    Direct sourceSnapshotId is the headless-friendly path; otherwise this is
    the single bridge point that reuses the service's private render helpers
    (same noqa: SLF001 convention as the store/job access in this module).
    """

    source_payload = source if isinstance(source, Mapping) else {}
    snapshot_id = str(source_payload.get("sourceSnapshotId") or "").strip()
    if snapshot_id:
        try:
            return asset_store.load_source_snapshot(snapshot_id)
        except (OSError, ValueError) as exc:
            raise _Sam3RunnerJobError(
                "ERR_ASSET_NOT_FOUND", "sam3_source_snapshot_missing"
            ) from exc

    scene = service._resolve_scene_for_arguments(dict(source_payload))  # noqa: SLF001
    png_bytes, _render_metadata = service._render_scene_to_png(scene)  # noqa: SLF001
    try:
        return asset_store.save_source_snapshot(
            png_bytes,
            snapshot_id=f"sam3-recipe-{job_id}",
            kind="visible_composite",
            width=int(scene.width),
            height=int(scene.height),
            page_id=str(source_payload.get("pageId") or "") or None,
            content_hash=hashlib.sha256(png_bytes).hexdigest(),
            metadata={
                "provider": "mcp",
                "source": "production_recipe_runner",
                "jobId": job_id,
            },
        )
    except (OSError, ValueError) as exc:
        raise _Sam3RunnerJobError("BACKEND_FAILED", "sam3_source_snapshot_save_failed") from exc


def score_artifact_candidates(arguments: Mapping[str, Any]) -> dict[str, Any]:
    domain = _required_string(arguments, "domain")
    quality_profile = _required_string(arguments, "qualityProfile")
    candidate_set = arguments.get("candidateSet")
    if domain not in _DOMAINS:
        return _error("ERR_UNSUPPORTED_DOMAIN", f"Unsupported production domain: {domain}")
    profile = _QUALITY_PROFILES.get(quality_profile)
    if profile is None:
        return _error(
            "ERR_QUALITY_PROFILE_NOT_FOUND",
            f"QualityProfile not found: {quality_profile}",
        )
    profile_domain = str(profile.get("domain") or "")
    if profile_domain and profile_domain != domain:
        return _error(
            "ERR_QUALITY_PROFILE_DOMAIN_MISMATCH",
            f"QualityProfile {quality_profile} is for domain {profile_domain}, not {domain}.",
        )
    if not isinstance(candidate_set, Mapping):
        return _error("ERR_INVALID_RECIPE_INPUT", "'candidateSet' must be an object.")
    if _is_sam3_manga_candidate_set(candidate_set):
        return _score_sam3_manga_mask_candidates(
            domain=domain,
            quality_profile=quality_profile,
            candidate_set=candidate_set,
        )
    return _score_generic_artifact_candidates(
        arguments=arguments,
        domain=domain,
        quality_profile=quality_profile,
        candidate_set=candidate_set,
    )


def _score_generic_artifact_candidates(
    *,
    arguments: Mapping[str, Any],
    domain: str,
    quality_profile: str,
    candidate_set: Mapping[str, Any],
) -> dict[str, Any]:
    candidates = candidate_set.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, str | bytes):
        return _error("ERR_INVALID_RECIPE_INPUT", "'candidateSet.candidates' must be an array.")
    checks = [
        str(check)
        for check in arguments.get("checks", _quality_profile_checks(quality_profile))
        if str(check)
    ]
    results = [
        _score_candidate(candidate, domain, quality_profile, checks)
        for candidate in candidates
        if isinstance(candidate, Mapping)
    ]
    selected = _select_candidate(results)
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "candidateSetId": str(candidate_set.get("candidateSetId") or ""),
        "domain": domain,
        "qualityProfile": quality_profile,
        "results": results,
        "selectedCandidateId": selected,
    }


def _is_sam3_manga_candidate_set(candidate_set: Mapping[str, Any]) -> bool:
    return str(candidate_set.get("kind") or "") == "sam3_manga_mask_candidates"


def _score_sam3_manga_mask_candidates(
    *,
    domain: str,
    quality_profile: str,
    candidate_set: Mapping[str, Any],
) -> dict[str, Any]:
    if domain != "comic_page":
        return _error(
            "ERR_UNSUPPORTED_DOMAIN",
            "SAM3 manga mask candidate scoring only supports comic_page.",
        )
    try:
        package = _sam3_candidate_set_to_package(candidate_set)
        canvas_size = _sam3_canvas_size(candidate_set)
        thresholds = _sam3_score_thresholds(candidate_set)
        scorecard = score_comic_mask_package_candidates(
            package,
            canvas_width=canvas_size[0] if canvas_size is not None else None,
            canvas_height=canvas_size[1] if canvas_size is not None else None,
            **thresholds,
        )
        projection = build_manga_mask_scorecard_production_projection(
            scorecard,
            quality_profile=quality_profile,
        )
    except (
        Sam3ComicMaskPackageError,
        Sam3MangaMaskScorecardError,
        Sam3MangaMaskScorecardProjectionError,
        ValueError,
        TypeError,
    ) as exc:
        return _error(
            "ERR_INVALID_RECIPE_INPUT",
            "SAM3 manga mask candidateSet is invalid.",
            details={"reason": str(exc)},
        )

    payload = projection.to_dict()
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "candidateSetId": payload["candidateSetId"],
        "domain": payload["domain"],
        "qualityProfile": payload["qualityProfile"],
        "selectedCandidateId": payload["selectedCandidateId"],
        "results": payload["results"],
        "sam3Scorecard": payload,
        "sam3VisualQa": _sam3_visual_qa_projection(payload),
    }


def _sam3_candidate_set_to_package(candidate_set: Mapping[str, Any]) -> Sam3ComicMaskPackage:
    candidate_set_id = _required_mapping_text(candidate_set, "candidateSetId")
    return Sam3ComicMaskPackage(
        package_id=_optional_mapping_text(candidate_set, "packageId") or candidate_set_id,
        source_document_id=_optional_mapping_text(candidate_set, "sourceDocumentId")
        or candidate_set_id,
        source_page_id=_optional_mapping_text(candidate_set, "sourcePageId") or candidate_set_id,
        panel_masks=tuple(
            _sam3_panel_mask_set(panel)
            for panel in _sequence_or_empty(candidate_set.get("panelMasks"))
        ),
        global_masks=tuple(
            _sam3_candidate_ref(candidate, panel_id=None)
            for candidate in _sequence_or_empty(candidate_set.get("globalMasks"))
        ),
    )


def _sam3_panel_mask_set(value: Any) -> Sam3PanelMaskSet:
    if not isinstance(value, Mapping):
        raise ValueError("panelMasks items must be objects.")
    panel_id = _required_mapping_text(value, "panelId")
    return Sam3PanelMaskSet(
        panel_id=panel_id,
        panel_bbox=_int_tuple4(value.get("panelBbox"), "panelBbox"),
        candidates=tuple(
            _sam3_candidate_ref(candidate, panel_id=panel_id)
            for candidate in _sequence_or_empty(value.get("candidates"))
        ),
    )


def _sam3_candidate_ref(value: Any, *, panel_id: str | None) -> Sam3ComicMaskCandidateRef:
    if not isinstance(value, Mapping):
        raise ValueError("SAM3 candidates must be objects.")
    candidate_id = _required_mapping_text(value, "candidateId")
    return Sam3ComicMaskCandidateRef(
        candidate_id=candidate_id,
        category=_required_mapping_text(value, "category"),
        mask_asset_id=f"sam3_mcp_candidate_{candidate_id}",
        bbox=_int_tuple4(value.get("bbox"), "bbox"),
        area_px=_as_int(value.get("areaPx")),
        score=_optional_float(value.get("score")),
        panel_id=panel_id or _optional_mapping_text(value, "panelId"),
        panel_local_bbox=_optional_int_tuple4(value.get("panelLocalBbox"), "panelLocalBbox"),
        accepted=bool(value.get("accepted", False)),
    )


def _sam3_canvas_size(candidate_set: Mapping[str, Any]) -> tuple[int, int] | None:
    canvas = candidate_set.get("canvas")
    if canvas is None:
        return None
    if not isinstance(canvas, Mapping):
        raise ValueError("candidateSet.canvas must be an object.")
    has_width = "width" in canvas
    has_height = "height" in canvas
    if has_width != has_height:
        raise ValueError("candidateSet.canvas requires both width and height.")
    if not has_width:
        return None
    width = _positive_int(canvas.get("width"), "canvas.width")
    height = _positive_int(canvas.get("height"), "canvas.height")
    return (width, height)


def _sam3_score_thresholds(candidate_set: Mapping[str, Any]) -> dict[str, Any]:
    thresholds = candidate_set.get("thresholds")
    if thresholds is None:
        return {}
    if not isinstance(thresholds, Mapping):
        raise ValueError("candidateSet.thresholds must be an object.")
    payload: dict[str, Any] = {}
    for source_key, target_key in (
        ("minConfidence", "min_confidence"),
        ("minAreaRatio", "min_area_ratio"),
        ("maxAreaRatio", "max_area_ratio"),
    ):
        if source_key in thresholds:
            payload[target_key] = thresholds[source_key]
    return payload


def _sam3_visual_qa_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    results = [dict(result) for result in payload.get("results", []) if isinstance(result, Mapping)]
    decision_counts = {"keep": 0, "manual_review": 0, "rework": 0, "reject": 0}
    failing_candidate_ids: list[str] = []
    rework_candidate_ids: list[str] = []
    manual_review_candidate_ids: list[str] = []
    for result in results:
        decision = str(result.get("decisionRecommendation") or "")
        if decision in decision_counts:
            decision_counts[decision] += 1
        candidate_id = str(result.get("candidateId") or "")
        if decision == "reject" and candidate_id:
            failing_candidate_ids.append(candidate_id)
        elif decision == "rework" and candidate_id:
            rework_candidate_ids.append(candidate_id)
        elif decision == "manual_review" and candidate_id:
            manual_review_candidate_ids.append(candidate_id)
    hard_failure_checks = _unique_items(
        check for result in results for check in _issue_checks(result, "hardFailures")
    )
    warning_checks = _unique_items(
        check for result in results for check in _issue_checks(result, "warnings")
    )
    status = _sam3_visual_qa_status(
        ok=bool(payload.get("ok", False)),
        has_hard_failures=bool(hard_failure_checks),
        has_warnings=bool(warning_checks),
    )
    return {
        "schemaVersion": _SAM3_VISUAL_QA_SCHEMA_VERSION,
        "candidateSetId": str(payload.get("candidateSetId") or ""),
        "qualityProfile": str(payload.get("qualityProfile") or ""),
        "status": status,
        "ok": bool(payload.get("ok", False)),
        "candidateCount": int(payload.get("candidateCount") or 0),
        "acceptedCandidateCount": int(payload.get("acceptedCandidateCount") or 0),
        "readyCandidateCount": int(payload.get("readyCandidateCount") or 0),
        "selectedCandidateId": payload.get("selectedCandidateId"),
        "decisionCounts": decision_counts,
        "hardFailureChecks": hard_failure_checks,
        "warningChecks": warning_checks,
        "failingCandidateIds": _unique_items(failing_candidate_ids),
        "reworkCandidateIds": _unique_items(rework_candidate_ids),
        "manualReviewCandidateIds": _unique_items(manual_review_candidate_ids),
        "recommendedActions": _unique_items(payload.get("recommendedActions", [])),
    }


def _issue_checks(result: Mapping[str, Any], key: str) -> list[str]:
    checks: list[str] = []
    for issue in result.get(key, []):
        if isinstance(issue, Mapping):
            check = str(issue.get("check") or "")
            if check:
                checks.append(check)
    return checks


def _sam3_visual_qa_status(
    *,
    ok: bool,
    has_hard_failures: bool,
    has_warnings: bool,
) -> str:
    if has_hard_failures:
        return "fail"
    if not ok:
        return "review_required"
    if has_warnings:
        return "warning"
    return "pass"


def _sequence_or_empty(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(value)
    raise ValueError("SAM3 candidate lists must be arrays.")


def _required_mapping_text(value: Mapping[str, Any], key: str) -> str:
    text = _optional_mapping_text(value, key)
    if text is None:
        raise ValueError(f"'{key}' is required.")
    return text


def _optional_mapping_text(value: Mapping[str, Any], key: str) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _int_tuple4(value: Any, field_name: str) -> tuple[int, int, int, int]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or len(value) != 4:
        raise ValueError(f"{field_name} must contain four values.")
    return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))


def _optional_int_tuple4(value: Any, field_name: str) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    return _int_tuple4(value, field_name)


def _positive_int(value: Any, field_name: str) -> int:
    number = _as_int(value)
    if number <= 0:
        raise ValueError(f"{field_name} must be positive.")
    return number


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def create_rework_plan(arguments: Mapping[str, Any]) -> dict[str, Any]:
    domain = _required_string(arguments, "domain")
    quality_profile = _required_string(arguments, "qualityProfile")
    failed_candidate = arguments.get("failedCandidate")
    qa_report = arguments.get("qaReport")
    if not isinstance(failed_candidate, Mapping):
        return _error("ERR_INVALID_RECIPE_INPUT", "'failedCandidate' must be an object.")
    if not isinstance(qa_report, Mapping):
        return _error("ERR_INVALID_RECIPE_INPUT", "'qaReport' must be an object.")

    failures = [item for item in qa_report.get("hardFailures", []) if isinstance(item, Mapping)]
    warnings = [item for item in qa_report.get("warnings", []) if isinstance(item, Mapping)]
    failure_checks = [str(item.get("check") or "") for item in failures + warnings]
    action = _rework_action_for_failures(domain, failure_checks)

    candidate_id = str(failed_candidate.get("candidateId") or "")
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "reworkPlanId": _stable_id(
            "rw",
            {
                "domain": domain,
                "qualityProfile": quality_profile,
                "candidateId": candidate_id,
                "qaReport": qa_report,
            },
        ),
        "targetCandidateId": candidate_id,
        "recommendedAction": action,
        "reason": _rework_reason(failure_checks),
        "promptPatch": _prompt_patch_for_failures(domain, failure_checks),
        "layoutPatch": _layout_patch_for_failures(domain, failure_checks),
        "expectedFixes": [check for check in failure_checks if check],
    }


def _rework_action_for_failures(domain: str, checks: Sequence[str]) -> str:
    action = "regenerate"
    if not any(checks):
        action = "manual_review"
    elif any("export" in check for check in checks):
        action = "adjust_export_settings"
    elif any(check in _SAM3_RESELECT_MASK_CHECKS for check in checks):
        action = "reselect_mask_candidate"
    elif any(check in _SAM3_REFINE_MASK_CHECKS for check in checks):
        action = "refine_mask_candidate"
    elif "candidate_score_missing" in checks:
        action = "review_candidate"
    elif "candidate_not_accepted" in checks:
        action = "accept_candidate"
    elif any("bubble" in check or "safe_area" in check for check in checks):
        action = "adjust_layout" if domain == "comic_page" else "regenerate"
    return action


def export_production_package(service: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
    job_id = _required_string(arguments, "jobId")
    store = service._production_store  # noqa: SLF001
    job = store.get_record("jobs", "jobId", job_id)
    if job is None:
        return _error("ERR_ASSET_NOT_FOUND", f"ProductionJob not found: {job_id}")
    if str(job.get("status") or "") != "completed":
        return _error(
            "ERR_JOB_NOT_COMPLETED",
            "Production package export requires a completed ProductionRecipe job.",
            details={"jobId": job_id, "status": str(job.get("status") or "")},
        )
    export_profile = str(arguments.get("exportProfile") or job.get("exportProfile") or "default")
    include_intermediate = bool(arguments.get("includeIntermediateArtifacts", False))
    include_lineage = bool(arguments.get("includeLineage", True))
    include_qa = bool(arguments.get("includeQaReports", True))
    include_rejected = bool(arguments.get("includeRejectedCandidates", False))
    domain = str(job.get("domain") or "")
    source_artifacts = _artifacts_for_job(store, job_id)
    scorecards = _scorecards_for_job(store, job_id)
    edges = _edges_for_job(store, job_id)
    artifacts = _package_artifacts(job, domain, export_profile)
    package = {
        "packageId": _stable_id(
            "pkg",
            {
                "jobId": job_id,
                "exportProfile": export_profile,
                "includeIntermediateArtifacts": include_intermediate,
                "includeLineage": include_lineage,
                "includeQaReports": include_qa,
                "includeRejectedCandidates": include_rejected,
            },
        ),
        "jobId": job_id,
        "status": "completed",
        "exportProfile": export_profile,
        "includeIntermediateArtifacts": include_intermediate,
        "includeLineage": include_lineage,
        "includeQaReports": include_qa,
        "includeRejectedCandidates": include_rejected,
        "artifacts": artifacts,
        "sourceArtifacts": source_artifacts,
        "scorecardIds": [str(card.get("scorecardId") or "") for card in scorecards],
        "lineageEdgeIds": [str(edge.get("edgeId") or "") for edge in edges],
    }
    sam3_manifest = _sam3_package_manifest(
        job,
        export_profile,
        artifacts,
        source_artifacts,
        package,
    )
    if sam3_manifest is not None:
        package["packageKind"] = sam3_manifest["manifestKind"]
        package["sam3PackageManifest"] = sam3_manifest
    store.upsert_records("packages", [package], key="packageId")
    return {"schemaVersion": RUNNER_SCHEMA_VERSION, **package}


def production_job_plan_resource(service: Any, job_id: str) -> dict[str, Any]:
    job = service._production_store.get_record("jobs", "jobId", job_id)  # noqa: SLF001
    if job is None:
        return _error("ERR_ASSET_NOT_FOUND", f"ProductionJob not found: {job_id}")
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "jobId": job_id,
        "plan": job.get("plan") or {},
    }


def production_job_candidates_resource(service: Any, job_id: str) -> dict[str, Any]:
    job = service._production_store.get_record("jobs", "jobId", job_id)  # noqa: SLF001
    if job is None:
        return _error("ERR_ASSET_NOT_FOUND", f"ProductionJob not found: {job_id}")
    artifacts = [
        artifact
        for artifact in service._production_store.read_records("artifacts")  # noqa: SLF001
        if str(dict(artifact.get("createdBy") or {}).get("jobId") or "") == job_id
    ]
    return {"schemaVersion": RUNNER_SCHEMA_VERSION, "jobId": job_id, "candidates": artifacts}


def production_job_sam3_candidates_resource(service: Any, job_id: str) -> dict[str, Any]:
    store = service._production_store  # noqa: SLF001
    job = store.get_record("jobs", "jobId", job_id)
    if job is None:
        return _error("ERR_ASSET_NOT_FOUND", f"ProductionJob not found: {job_id}")
    candidate_packages = _sam3_candidate_package_summaries(_packages_for_job(store, job_id))
    quoted_job_id = quote(job_id, safe="")
    return {
        "schemaVersion": _SAM3_PRODUCTION_JOB_CANDIDATES_SCHEMA_VERSION,
        "jobId": job_id,
        "recipeId": str(job.get("recipeId") or ""),
        "status": str(job.get("status") or ""),
        "available": bool(candidate_packages),
        "candidatePackageCount": len(candidate_packages),
        "candidatePackages": candidate_packages,
        "relatedResources": {
            "job": f"loomstroke://production/job/{quoted_job_id}",
            "exports": f"loomstroke://production/job/{quoted_job_id}/exports",
            "lineage": f"loomstroke://production/job/{quoted_job_id}/lineage",
            "scorecard": f"loomstroke://production/job/{quoted_job_id}/scorecard",
        },
    }


def production_artifact_masks_resource(service: Any, artifact_id: str) -> dict[str, Any]:
    service.get_production_artifact(artifact_id)
    store = service._production_store  # noqa: SLF001
    mask_packages = _sam3_artifact_mask_package_summaries(
        _sam3_packages_for_source_artifact(store, artifact_id)
    )
    quoted_artifact_id = quote(artifact_id, safe="")
    return {
        "schemaVersion": _SAM3_PRODUCTION_ARTIFACT_MASKS_SCHEMA_VERSION,
        "artifactId": artifact_id,
        "available": bool(mask_packages),
        "maskPackageCount": len(mask_packages),
        "jobIds": _unique_items(
            package["jobId"] for package in mask_packages if package.get("jobId")
        ),
        "scorecardIds": _unique_items(
            scorecard_id
            for package in mask_packages
            for scorecard_id in package.get("scorecardIds", [])
        ),
        "lineageEdgeIds": _unique_items(
            edge_id for package in mask_packages for edge_id in package.get("lineageEdgeIds", [])
        ),
        "maskPackages": mask_packages,
        "relatedResources": {
            "artifact": f"loomstroke://production/artifact/{quoted_artifact_id}",
            "lineage": f"loomstroke://production/lineage/{quoted_artifact_id}",
        },
    }


def production_job_scorecard_resource(service: Any, job_id: str) -> dict[str, Any]:
    cards = [
        card
        for card in service._production_store.read_records("scorecards")  # noqa: SLF001
        if str(card.get("jobId") or "") == job_id
    ]
    return {"schemaVersion": RUNNER_SCHEMA_VERSION, "jobId": job_id, "scorecards": cards}


def production_job_exports_resource(service: Any, job_id: str) -> dict[str, Any]:
    store = service._production_store  # noqa: SLF001
    job = store.get_record("jobs", "jobId", job_id)
    packages = [
        package
        for package in store.read_records("packages")
        if str(package.get("jobId") or "") == job_id
    ]
    payload = {"schemaVersion": RUNNER_SCHEMA_VERSION, "jobId": job_id, "packages": packages}
    if job is not None:
        sam3_projection = _sam3_production_control_projection(job, packages)
        if sam3_projection is not None:
            payload["sam3ProductionControlProjection"] = sam3_projection
    return payload


def production_job_lineage_resource(service: Any, job_id: str) -> dict[str, Any]:
    store = service._production_store  # noqa: SLF001
    job = store.get_record("jobs", "jobId", job_id)
    if job is None:
        return _error("ERR_ASSET_NOT_FOUND", f"ProductionJob not found: {job_id}")
    edges = _edges_for_job(store, job_id)
    source_artifacts = _artifacts_for_job(store, job_id)
    payload = {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "jobId": job_id,
        "edges": edges,
        "sourceArtifacts": source_artifacts,
    }
    sam3_projection = _sam3_lineage_projection(
        job,
        edges,
        source_artifacts,
        _scorecards_for_job(store, job_id),
        _packages_for_job(store, job_id),
    )
    if sam3_projection is not None:
        payload["sam3LineageProjection"] = sam3_projection
    return payload


def production_job_rework_resource(service: Any, job_id: str) -> dict[str, Any]:
    cards = production_job_scorecard_resource(service, job_id)["scorecards"]
    plans = []
    for card in cards:
        for result in card.get("results", []):
            if isinstance(result, Mapping) and result.get("decisionRecommendation") in {
                "reject",
                "rework",
            }:
                plans.append(
                    create_rework_plan(
                        {
                            "domain": card.get("domain"),
                            "qualityProfile": card.get("qualityProfile"),
                            "failedCandidate": result,
                            "qaReport": {
                                "hardFailures": result.get("hardFailures", []),
                                "warnings": result.get("warnings", []),
                            },
                        }
                    )
                )
    return {"schemaVersion": RUNNER_SCHEMA_VERSION, "jobId": job_id, "reworkPlans": plans}


def _artifacts_for_job(store: Any, job_id: str) -> list[dict[str, Any]]:
    return [
        dict(artifact)
        for artifact in store.read_records("artifacts")
        if str(dict(artifact.get("createdBy") or {}).get("jobId") or "") == job_id
    ]


def _scorecards_for_job(store: Any, job_id: str) -> list[dict[str, Any]]:
    return [
        dict(card)
        for card in store.read_records("scorecards")
        if str(card.get("jobId") or "") == job_id
    ]


def _packages_for_job(store: Any, job_id: str) -> list[dict[str, Any]]:
    return [
        dict(package)
        for package in store.read_records("packages")
        if str(package.get("jobId") or "") == job_id
    ]


def _edges_for_job(store: Any, job_id: str) -> list[dict[str, Any]]:
    return [
        dict(edge)
        for edge in store.read_records("edges")
        if str(edge.get("fromArtifactId") or "") == job_id
        or str(dict(edge.get("metadata") or {}).get("jobId") or "") == job_id
    ]


def quality_profiles_resource() -> dict[str, Any]:
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "qualityProfiles": list(_QUALITY_PROFILES.values()),
    }


def quality_profile_resource(profile_id: str) -> dict[str, Any]:
    profile = _QUALITY_PROFILES.get(profile_id)
    if profile is None:
        return _error("ERR_QUALITY_PROFILE_NOT_FOUND", f"QualityProfile not found: {profile_id}")
    return {"schemaVersion": RUNNER_SCHEMA_VERSION, "qualityProfile": profile}


def _update_job_status(
    store: Any,
    mcp_job: JobRecord,
    job: dict[str, Any],
    status: str,
    *,
    progress: float | None,
    current_step: str | None = None,
) -> None:
    job["status"] = status
    job["progress"] = progress
    if current_step is not None:
        job["currentStep"] = current_step
    job["updatedAt"] = _now_iso()
    store.add_job(job)
    mcp_job.status = status
    mcp_job.progress = progress
    if current_step is not None:
        mcp_job.metadata["currentStep"] = current_step
    mcp_job.touch()


def _status_for_step(step_id: str) -> str:
    status_by_token = {
        "generate": "generating",
        "import": "importing_assets",
        "score": "scoring",
        "qa": "scoring",
        "rework": "reworking",
        "export": "exporting",
        "render": "assembling",
        "assemble": "assembling",
        "apply": "assembling",
    }
    for token, status in status_by_token.items():
        if token in step_id:
            return status
    return "running"


async def _submit_generation_candidates(
    service: Any,
    job: Mapping[str, Any],
    step: Mapping[str, Any],
) -> list[dict[str, Any]]:
    submitter = getattr(service, "_generation_submitter", None)
    if submitter is None:
        return []
    normalized = dict(job.get("normalizedInput") or {})
    policy = dict(normalized.get("candidatePolicy") or {})
    count = _clamp_int(step.get("candidateCount") or policy.get("candidateCount"), 1, 12, 1)
    results: list[dict[str, Any]] = []
    for index in range(count):
        payload = {
            "source": "production_recipe_runner",
            "jobId": job.get("jobId"),
            "recipeId": job.get("recipeId"),
            "domain": job.get("domain"),
            "candidateIndex": index,
            "brief": normalized.get("brief"),
            "constraints": normalized.get("constraints"),
            "qualityProfile": normalized.get("qualityProfile"),
            "applyToCanvas": False,
        }
        result = submitter(payload)
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, Mapping):
            results.append(pct.scrub_for_pct(dict(result)))
        else:
            results.append({"status": "submitted", "candidateIndex": index})
    return results


def _candidate_artifacts(
    job: Mapping[str, Any],
    step: Mapping[str, Any],
    external_results: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    count = max(
        len(external_results),
        _clamp_int(step.get("candidateCount"), 1, 12, 1),
    )
    artifacts = []
    for index in range(count):
        external = dict(external_results[index]) if index < len(external_results) else {}
        artifact_id = _stable_id(
            "prod_art",
            {"jobId": job.get("jobId"), "candidateIndex": index, "external": external},
        )
        asset_id = str(
            external.get("assetId")
            or external.get("resultAssetId")
            or _stable_id("asset", {"artifactId": artifact_id})
        )
        artifacts.append(
            {
                "schemaVersion": RUNNER_SCHEMA_VERSION,
                "artifactId": artifact_id,
                "assetId": asset_id,
                "artifactKind": _artifact_kind_for_domain(str(job.get("domain") or "")),
                "domain": job.get("domain"),
                "mimeType": _mime_for_domain(str(job.get("domain") or "")),
                "metadata": _candidate_metadata_for_external(index, external),
                "createdBy": {
                    "jobId": job.get("jobId"),
                    "stepId": step.get("stepId"),
                    "recipeId": job.get("recipeId"),
                },
                "createdAt": _now_iso(),
            }
        )
    return artifacts


def _candidate_metadata_for_external(
    index: int,
    external: Mapping[str, Any],
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    external_metadata = external.get("metadata")
    if isinstance(external_metadata, Mapping):
        metadata.update(pct.scrub_for_pct(dict(external_metadata)))

    dimensions = external.get("dimensions")
    if isinstance(dimensions, Mapping):
        width = _as_int(dimensions.get("width"))
        height = _as_int(dimensions.get("height"))
        if width:
            metadata["width"] = width
        if height:
            metadata["height"] = height
    for key in ("width", "height"):
        value = _as_int(external.get(key))
        if value:
            metadata[key] = value

    asset_id = external.get("assetId") or external.get("resultAssetId")
    if asset_id:
        metadata["assetId"] = str(asset_id)
    external_job_id = external.get("jobId") or external.get("externalJobId")
    if external_job_id:
        metadata["externalJobId"] = str(external_job_id)
    if external.get("status"):
        metadata["externalStatus"] = str(external.get("status"))

    metadata["candidateIndex"] = index
    metadata["placeholder"] = not bool(external)
    metadata["externalResult"] = pct.scrub_for_pct(dict(external))
    return pct.scrub_for_pct(metadata)


def _placeholder_artifacts(job: Mapping[str, Any]) -> list[dict[str, Any]]:
    artifact_id = _stable_id("prod_art", {"jobId": job.get("jobId"), "placeholder": True})
    return [
        {
            "schemaVersion": RUNNER_SCHEMA_VERSION,
            "artifactId": artifact_id,
            "assetId": _stable_id("asset", {"artifactId": artifact_id}),
            "artifactKind": _artifact_kind_for_domain(str(job.get("domain") or "")),
            "domain": job.get("domain"),
            "mimeType": _mime_for_domain(str(job.get("domain") or "")),
            "metadata": {"placeholder": True},
            "createdBy": {
                "jobId": job.get("jobId"),
                "stepId": "runner_placeholder",
                "recipeId": job.get("recipeId"),
            },
            "createdAt": _now_iso(),
        }
    ]


def _artifact_from_3d_source(
    service: Any,
    job: Mapping[str, Any],
    step: Mapping[str, Any],
) -> dict[str, Any]:
    metrics = _extract_3d_source_metadata(service, job)
    artifact_id = _stable_id(
        "prod_art",
        {"jobId": job.get("jobId"), "sourcePath": metrics.get("sourcePath")},
    )
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "artifactId": artifact_id,
        "assetId": _stable_id("asset", {"artifactId": artifact_id}),
        "artifactKind": "mesh_3d",
        "domain": "game_asset_3d",
        "mimeType": _mime_for_domain("game_asset_3d"),
        "metadata": metrics,
        "createdBy": {
            "jobId": job.get("jobId"),
            "stepId": step.get("stepId"),
            "recipeId": job.get("recipeId"),
        },
        "createdAt": _now_iso(),
    }


def _extract_3d_source_metadata(service: Any, job: Mapping[str, Any]) -> dict[str, Any]:
    raw_path = _source_path_from_job(job)
    if not raw_path:
        return {
            "file_loadable": False,
            "loadError": "source_path_not_set",
            "placeholder": True,
        }
    try:
        source = _resolve_rooted_input_path(service, raw_path)
        from loomstroke.core.assets_3d.file_io import load_scene  # noqa: PLC0415

        scene = load_scene(source)
        primitive_count = 0
        vertex_count = 0
        triangle_count = 0
        has_uvs = False
        has_normals = False
        material_ids: set[str] = set()
        for mesh in scene.meshes:
            for primitive in mesh.primitives:
                primitive_count += 1
                vertex_count += len(primitive.vertices)
                triangle_count += int(primitive.indices.reshape((-1, 3)).shape[0])
                has_uvs = has_uvs or primitive.uvs is not None
                has_normals = has_normals or primitive.normals is not None
                if primitive.material_id:
                    material_ids.add(str(primitive.material_id))
        low, high = scene.bounds()
        constraints = dict(dict(job.get("normalizedInput") or {}).get("constraints") or {})
        return {
            "file_loadable": True,
            "sourcePath": str(source),
            "sourceFormat": source.suffix.lower().lstrip("."),
            "meshCount": len(scene.meshes),
            "primitiveCount": primitive_count,
            "vertexCount": vertex_count,
            "triangles": triangle_count,
            "has_uvs": has_uvs,
            "has_normals": has_normals,
            "materialCount": len(scene.materials) or len(material_ids),
            "textureCount": len(scene.textures),
            "boundingBox": {
                "min": [round(float(value), 6) for value in low.tolist()],
                "max": [round(float(value), 6) for value in high.tolist()],
            },
            "maxTriangles": _clamp_int(constraints.get("polyBudget"), 1, 10_000_000, 1500),
            "maxMaterials": _clamp_int(constraints.get("maxMaterials"), 1, 1024, 3),
            "maxTextureSize": _clamp_int(constraints.get("textureSize"), 1, 65536, 1024),
            "exportProfileValid": True,
        }
    except Exception as exc:
        return {
            "file_loadable": False,
            "sourcePath": str(raw_path),
            "loadError": exc.__class__.__name__,
            "loadErrorMessage": str(exc),
        }


def _source_path_from_job(job: Mapping[str, Any]) -> str | None:
    normalized = dict(job.get("normalizedInput") or {})
    containers = [
        normalized.get("target"),
        normalized.get("constraints"),
        normalized,
    ]
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in ("sourcePath", "inputPath", "assetPath", "path"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _resolve_rooted_input_path(service: Any, raw_path: str) -> Path:
    raw = Path(raw_path).expanduser()
    if any(part == ".." for part in raw.parts):
        raise PermissionError("source_path_traversal_not_allowed")
    roots = [Path(root).resolve(strict=False) for root in getattr(service.safety, "roots", ())]
    if not roots:
        raise PermissionError("roots_required_for_source_path")

    candidates = [raw] if raw.is_absolute() else [root / raw for root in roots]
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if any(_is_relative_to(resolved, root) for root in roots):
            if not resolved.exists():
                raise FileNotFoundError(str(resolved))
            if not resolved.is_file():
                raise IsADirectoryError(str(resolved))
            return resolved
    raise PermissionError("source_path_outside_roots")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def _scorecard_for_job(
    job: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = dict(job.get("normalizedInput") or {})
    quality_profile = str(normalized.get("qualityProfile") or "")
    result = score_artifact_candidates(
        {
            "domain": job.get("domain"),
            "qualityProfile": quality_profile,
            "candidateSet": {
                "candidateSetId": _stable_id("cset", {"jobId": job.get("jobId")}),
                "candidates": [
                    {
                        "candidateId": f"cand_{index + 1}",
                        "assetId": artifact.get("assetId"),
                        "artifactId": artifact.get("artifactId"),
                        "metadata": dict(artifact.get("metadata") or {}),
                    }
                    for index, artifact in enumerate(artifacts or _placeholder_artifacts(job))
                ],
            },
        }
    )
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "scorecardId": _stable_id("scorecard", {"jobId": job.get("jobId")}),
        "jobId": job.get("jobId"),
        "domain": job.get("domain"),
        "qualityProfile": quality_profile,
        **{key: value for key, value in result.items() if key != "schemaVersion"},
        "createdAt": _now_iso(),
    }


def _lineage_edges_for_job(
    job: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "schemaVersion": RUNNER_SCHEMA_VERSION,
            "edgeId": _stable_id(
                "prod_edge",
                {"jobId": job.get("jobId"), "artifactId": artifact.get("artifactId")},
            ),
            "fromArtifactId": str(job.get("jobId")),
            "toArtifactId": artifact.get("artifactId"),
            "edgeKind": "created_by_production_recipe",
            "metadata": {
                "recipeId": job.get("recipeId"),
                "domain": job.get("domain"),
            },
            "createdAt": _now_iso(),
        }
        for artifact in artifacts
    ]


def _package_id_for_job(job: Mapping[str, Any]) -> str:
    export_profile = str(job.get("exportProfile") or "asset_package")
    return _stable_id("pkg", {"jobId": job.get("jobId"), "exportProfile": export_profile})


def _package_record_for_job(
    job: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    export_profile = str(job.get("exportProfile") or "asset_package")
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "packageId": _package_id_for_job(job),
        "jobId": job.get("jobId"),
        "status": "completed",
        "exportProfile": export_profile,
        "artifacts": [
            {
                "kind": artifact.get("artifactKind"),
                "assetId": artifact.get("assetId"),
                "artifactId": artifact.get("artifactId"),
            }
            for artifact in artifacts
        ],
        "createdAt": _now_iso(),
    }


def _artifact_kind_for_domain(domain: str) -> str:
    return {
        "comic_page": "comic_panel",
        "character_sheet": "character_sheet",
        "character_design": "character_design",
        "game_asset_3d": "mesh_3d",
        "texture_set": "texture_set",
        "sprite_sheet": "sprite_sheet",
    }.get(domain, "bitmap_image")


def _mime_for_domain(domain: str) -> str:
    if domain == "game_asset_3d":
        return "model/gltf-binary"
    return "image/png"


def _recipes() -> list[dict[str, Any]]:
    recipes = [
        _recipe(
            "comic_page.improve",
            "comic_page",
            "Improve Comic Page",
            "Improve layout, candidates, speech bubbles, typesetting, QA, and export.",
            ["comic_draft_preview", "comic_publish_preview"],
            ["comic_publish_preview_package"],
            requires_external=True,
        ),
        _recipe(
            "comic_page.create",
            "comic_page",
            "Create Comic Page",
            "Plan, generate, score, assemble, QA, and export a comic page.",
            ["comic_draft_preview", "comic_publish_preview"],
            ["comic_publish_preview_package"],
            requires_external=True,
        ),
        _recipe(
            "character.design.create",
            "character_design",
            "Create Character Design",
            "Create candidate character designs from a brief and Production Bible rules.",
            ["character_design_preview"],
            ["character_package"],
            requires_external=True,
        ),
        _recipe(
            "character.sheet.create",
            "character_sheet",
            "Create Character Sheet",
            "Create front, side, back, and expression views with consistency QA.",
            ["character_sheet_preview"],
            ["character_package"],
            requires_external=True,
        ),
        _recipe(
            "game_asset_3d.create",
            "game_asset_3d",
            "Create Game-ready 3D Asset",
            "Generate or process a 3D asset, validate geometry, preview, and export.",
            ["game_realtime_lowpoly", "game_realtime_midpoly"],
            ["game_asset_3d_package"],
            requires_external=True,
        ),
        _recipe(
            "game_asset_3d.improve",
            "game_asset_3d",
            "Improve Game-ready 3D Asset",
            "Validate, rework, preview, and package an existing 3D asset.",
            ["game_realtime_lowpoly", "game_realtime_midpoly"],
            ["game_asset_3d_package"],
            requires_external=False,
        ),
        _recipe(
            "sprite_sheet.create",
            "sprite_sheet",
            "Create Sprite Sheet",
            "Generate, score, and package sprite sheet assets.",
            ["sprite_sheet_preview"],
            ["sprite_sheet_package"],
            requires_external=True,
        ),
        _recipe(
            "texture_set.create",
            "texture_set",
            "Create Texture Set",
            "Generate and validate texture maps for material workflows.",
            ["texture_pbr_preview"],
            ["texture_set_package"],
            requires_external=True,
        ),
        _recipe(
            "background_art.create",
            "background_art",
            "Create Background Art",
            "Generate and QA background art candidates.",
            ["background_art_preview"],
            ["asset_package"],
            requires_external=True,
        ),
        _recipe(
            "item_icon.create",
            "item_icon",
            "Create Item Icon",
            "Generate and QA item icon candidates.",
            ["item_icon_preview"],
            ["asset_package"],
            requires_external=True,
        ),
    ]
    recipes.extend(
        descriptor.to_runner_recipe() for descriptor in sam3_production_recipe_descriptors()
    )
    return recipes


def _sam3_recipe_descriptor_by_id(
    recipe_id: str,
) -> Sam3ProductionRecipeDescriptor | None:
    for descriptor in sam3_production_recipe_descriptors():
        if descriptor.recipe_id == recipe_id:
            return descriptor
    return None


def _recipe(
    recipe_id: str,
    domain: str,
    display_name: str,
    description: str,
    quality_profiles: Sequence[str],
    export_profiles: Sequence[str],
    *,
    requires_external: bool,
) -> dict[str, Any]:
    return {
        "recipeId": recipe_id,
        "domain": domain,
        "version": "1.0.0",
        "displayName": display_name,
        "description": description,
        "modes": ["dry_run", "submit"],
        "defaultQualityProfile": quality_profiles[0],
        "qualityProfiles": list(quality_profiles),
        "supportedQualityProfiles": list(quality_profiles),
        "supportedExportProfiles": list(export_profiles),
        "requiresExternalServices": requires_external,
        "supportsRework": True,
        "supportsCandidateGeneration": requires_external,
        "experimental": False,
    }


_QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "comic_draft_preview": {
        "qualityProfileId": "comic_draft_preview",
        "domain": "comic_page",
        "displayName": "Comic Draft Preview",
        "hardChecks": ["panel_resolution", "export_profile"],
        "softChecks": [
            "comic_plan_geometry",
            "speech_bubble_fit",
            "text_safe_area",
            "bubble_overlap",
            "composition_balance",
            "visual_readability",
        ],
        "thresholds": {
            "minPanelResolution": 512,
            "maxTextOverflowRate": 0.1,
            "maxBubbleOverlapRate": 0.1,
        },
        "weights": {"panel_resolution": 1.0, "visual_readability": 0.8},
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 1},
    },
    "comic_publish_preview": {
        "qualityProfileId": "comic_publish_preview",
        "domain": "comic_page",
        "displayName": "Comic Publish Preview",
        "hardChecks": [
            "comic_plan_geometry",
            "panel_resolution",
            "speech_bubble_fit",
            "text_safe_area",
            "bubble_overlap",
            "export_profile",
        ],
        "softChecks": [
            "composition_balance",
            "contrast",
            "visual_readability",
            "no_baked_in_text",
        ],
        "thresholds": {
            "minPanelResolution": 768,
            "maxTextOverflowRate": 0,
            "maxBubbleOverlapRate": 0.05,
            "minTextAreaContrast": 4.5,
        },
        "weights": {
            "speech_bubble_fit": 1.5,
            "text_safe_area": 1.5,
            "panel_resolution": 1.2,
            "composition_balance": 0.8,
            "contrast": 1.0,
        },
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 2},
    },
    "character_sheet_preview": {
        "qualityProfileId": "character_sheet_preview",
        "domain": "character_sheet",
        "displayName": "Character Sheet Preview",
        "hardChecks": [
            "required_views_present",
            "image_resolution",
            "transparent_background_if_required",
            "export_profile",
        ],
        "softChecks": [
            "character_consistency",
            "silhouette_consistency",
            "palette_consistency",
            "outfit_rule_consistency",
            "expression_variety",
            "reference_similarity",
        ],
        "thresholds": {"minViewResolution": 768, "requiredViews": ["front", "side", "back"]},
        "weights": {},
        "defaultCandidatePolicy": {"candidateCount": 6, "maxReworkAttempts": 2},
    },
    "character_design_preview": {
        "qualityProfileId": "character_design_preview",
        "domain": "character_design",
        "displayName": "Character Design Preview",
        "hardChecks": ["image_resolution", "export_profile"],
        "softChecks": [
            "character_consistency",
            "silhouette_readability",
            "palette_consistency",
            "outfit_rule_consistency",
            "reference_similarity",
        ],
        "thresholds": {"minImageResolution": 768},
        "weights": {"character_consistency": 1.2, "silhouette_readability": 1.0},
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 2},
    },
    "game_realtime_lowpoly": {
        "qualityProfileId": "game_realtime_lowpoly",
        "domain": "game_asset_3d",
        "displayName": "Game Realtime Low Poly",
        "hardChecks": [
            "file_loadable",
            "triangle_budget",
            "has_uvs",
            "has_normals",
            "origin_and_scale",
            "texture_resolution",
            "export_format",
        ],
        "softChecks": [
            "non_manifold_geometry",
            "material_count",
            "lod_presence",
            "collision_mesh_presence",
            "silhouette_readability",
            "style_match",
        ],
        "thresholds": {
            "maxTriangles": 1500,
            "maxMaterials": 3,
            "maxTextureSize": 1024,
            "unitScale": "meter",
            "preferredOrigin": "bottom_center",
        },
        "weights": {},
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 2},
    },
    "game_realtime_midpoly": {
        "qualityProfileId": "game_realtime_midpoly",
        "domain": "game_asset_3d",
        "displayName": "Game Realtime Mid Poly",
        "hardChecks": [
            "file_loadable",
            "triangle_budget",
            "has_uvs",
            "has_normals",
            "origin_and_scale",
            "texture_resolution",
            "export_format",
        ],
        "softChecks": [
            "non_manifold_geometry",
            "material_count",
            "lod_presence",
            "collision_mesh_presence",
            "silhouette_readability",
            "style_match",
        ],
        "thresholds": {
            "maxTriangles": 8000,
            "maxMaterials": 6,
            "maxTextureSize": 2048,
            "unitScale": "meter",
            "preferredOrigin": "bottom_center",
        },
        "weights": {},
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 2},
    },
    "sprite_sheet_preview": {
        "qualityProfileId": "sprite_sheet_preview",
        "domain": "sprite_sheet",
        "displayName": "Sprite Sheet Preview",
        "hardChecks": ["image_resolution", "export_profile"],
        "softChecks": [
            "frame_grid_alignment",
            "transparent_background_if_required",
            "animation_consistency",
        ],
        "thresholds": {"minImageResolution": 512},
        "weights": {"frame_grid_alignment": 1.2},
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 2},
    },
    "texture_pbr_preview": {
        "qualityProfileId": "texture_pbr_preview",
        "domain": "texture_set",
        "displayName": "Texture PBR Preview",
        "hardChecks": ["image_resolution", "export_profile"],
        "softChecks": [
            "required_maps_present",
            "texture_resolution",
            "tileability",
            "channel_packing",
        ],
        "thresholds": {"minTextureResolution": 1024, "maxTextureSize": 4096},
        "weights": {"required_maps_present": 1.5, "tileability": 1.0},
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 2},
    },
    "background_art_preview": {
        "qualityProfileId": "background_art_preview",
        "domain": "background_art",
        "displayName": "Background Art Preview",
        "hardChecks": ["image_resolution", "export_profile"],
        "softChecks": ["composition_balance", "perspective_consistency", "style_match"],
        "thresholds": {"minImageResolution": 1024},
        "weights": {"composition_balance": 1.0, "style_match": 1.0},
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 2},
    },
    "item_icon_preview": {
        "qualityProfileId": "item_icon_preview",
        "domain": "item_icon",
        "displayName": "Item Icon Preview",
        "hardChecks": ["image_resolution", "export_profile"],
        "softChecks": [
            "silhouette_readability",
            "transparent_background_if_required",
            "style_match",
        ],
        "thresholds": {"minImageResolution": 512},
        "weights": {"silhouette_readability": 1.2},
        "defaultCandidatePolicy": {"candidateCount": 4, "maxReworkAttempts": 2},
    },
}


def _recipe_by_id(recipe_id: str) -> dict[str, Any] | None:
    for recipe in _recipes():
        if recipe["recipeId"] == recipe_id:
            return recipe
    return None


def _normalize_input(
    recipe: Mapping[str, Any],
    raw_input: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if _sam3_recipe_descriptor_by_id(str(recipe.get("recipeId") or "")) is not None:
        return _normalize_sam3_input(recipe, raw_input)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    target = raw_input.get("target")
    brief = raw_input.get("brief")
    if not isinstance(target, Mapping):
        errors.append(_issue("ERR_INVALID_RECIPE_INPUT", "'target' must be an object."))
        target = {}
    if not isinstance(brief, Mapping):
        errors.append(_issue("ERR_INVALID_RECIPE_INPUT", "'brief' must be an object."))
        brief = {}
    quality_profile = str(
        raw_input.get("qualityProfile") or recipe.get("defaultQualityProfile") or ""
    )
    supported = set(recipe.get("supportedQualityProfiles", []))
    if quality_profile not in supported:
        errors.append(
            _issue(
                "ERR_QUALITY_PROFILE_NOT_FOUND",
                f"QualityProfile is not supported by {recipe['recipeId']}: {quality_profile}",
            )
        )
    constraints = raw_input.get("constraints")
    if constraints is None:
        constraints = {}
    if not isinstance(constraints, Mapping):
        errors.append(_issue("ERR_INVALID_RECIPE_INPUT", "'constraints' must be an object."))
        constraints = {}
    candidate_policy = raw_input.get("candidatePolicy")
    if candidate_policy is None:
        candidate_policy = _default_candidate_policy(quality_profile)
    if not isinstance(candidate_policy, Mapping):
        errors.append(_issue("ERR_INVALID_RECIPE_INPUT", "'candidatePolicy' must be an object."))
        candidate_policy = _default_candidate_policy(quality_profile)
    normalized_policy = {
        "candidateCount": _clamp_int(candidate_policy.get("candidateCount"), 1, 12, 4),
        "maxReworkAttempts": _clamp_int(candidate_policy.get("maxReworkAttempts"), 0, 5, 2),
    }
    if not recipe.get("requiresExternalServices") and normalized_policy["candidateCount"] > 1:
        warnings.append(
            _issue(
                "WARN_LOCAL_RECIPE_CANDIDATES",
                "Local-only recipe planning records candidate policy but does not submit "
                "generation.",
            )
        )
    return (
        {
            "recipeId": recipe["recipeId"],
            "domain": recipe["domain"],
            "target": pct.scrub_for_pct(dict(target)),
            "brief": pct.scrub_for_pct(dict(brief)),
            "constraints": pct.scrub_for_pct(dict(constraints)),
            "qualityProfile": quality_profile,
            "exportProfile": str(raw_input.get("exportProfile") or _default_export_profile(recipe)),
            "candidatePolicy": normalized_policy,
        },
        warnings,
        errors,
    )


def _normalize_sam3_input(
    recipe: Mapping[str, Any],
    raw_input: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize SAM3 recipe input (source / prompts instead of target/brief)."""

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    source = raw_input.get("source")
    if source is None:
        source = {}
    if not isinstance(source, Mapping):
        errors.append(_issue("ERR_INVALID_RECIPE_INPUT", "'source' must be an object."))
        source = {}
    normalized_source = {
        "sourceSnapshotId": str(source.get("sourceSnapshotId") or "") or None,
        "documentId": str(source.get("documentId") or "") or None,
        "pageId": str(source.get("pageId") or "") or None,
        "scope": str(source.get("scope") or "visible_composite"),
    }

    prompts = _normalize_sam3_prompts(
        raw_input.get("prompts"),
        errors,
        default_prompts=_sam3_default_prompts(str(recipe["recipeId"])),
    )

    quality_profile = str(
        raw_input.get("qualityProfile") or recipe.get("defaultQualityProfile") or ""
    )
    supported = set(recipe.get("supportedQualityProfiles", []))
    if quality_profile not in supported:
        errors.append(
            _issue(
                "ERR_QUALITY_PROFILE_NOT_FOUND",
                f"QualityProfile is not supported by {recipe['recipeId']}: {quality_profile}",
            )
        )
    export_profile = str(raw_input.get("exportProfile") or _default_export_profile(recipe))
    supported_exports = {str(profile) for profile in recipe.get("supportedExportProfiles", [])}
    if supported_exports and export_profile not in supported_exports:
        # Cross-profile submits would produce a manifest whose packageKind and
        # artifacts (descriptor-derived) disagree — reject at dry_run.
        errors.append(
            _issue(
                "ERR_INVALID_RECIPE_INPUT",
                f"ExportProfile is not supported by {recipe['recipeId']}: {export_profile}",
            )
        )
    candidate_policy = raw_input.get("candidatePolicy")
    if candidate_policy is None or not isinstance(candidate_policy, Mapping):
        candidate_policy = _default_candidate_policy(quality_profile)
    normalized_policy = {
        "candidateCount": _clamp_int(candidate_policy.get("candidateCount"), 1, 12, 4),
        "maxReworkAttempts": _clamp_int(candidate_policy.get("maxReworkAttempts"), 0, 5, 2),
    }

    output = raw_input.get("output")
    output = output if isinstance(output, Mapping) else {}
    return (
        {
            "recipeId": recipe["recipeId"],
            "domain": recipe["domain"],
            "source": normalized_source,
            "prompts": prompts,
            "maxCandidatesPerPrompt": _clamp_int(
                raw_input.get("maxCandidatesPerPrompt"), 1, 64, 16
            ),
            "output": {
                "kind": str(output.get("kind") or "mask_package"),
                "includePreview": bool(output.get("includePreview", False)),
            },
            "qualityProfile": quality_profile,
            "exportProfile": export_profile,
            "candidatePolicy": normalized_policy,
        },
        warnings,
        errors,
    )


def _normalize_sam3_prompts(
    raw_prompts: Any,
    errors: list[dict[str, Any]],
    *,
    default_prompts: Sequence[str] = _SAM3_DEFAULT_PROMPTS,
) -> list[str]:
    """Normalize the prompt list (strip / dedupe / defaults) and record errors."""

    prompts: list[str] = []
    if raw_prompts is None:
        prompts = list(default_prompts)
    elif isinstance(raw_prompts, Sequence) and not isinstance(raw_prompts, str | bytes):
        for item in raw_prompts:
            text = str(item or "").strip()
            if text and text not in prompts:
                prompts.append(text)
        if not prompts:
            prompts = list(default_prompts)
    else:
        errors.append(_issue("ERR_INVALID_RECIPE_INPUT", "'prompts' must be a list of strings."))
        prompts = list(default_prompts)
    if len(prompts) > _SAM3_MAX_PROMPTS:
        errors.append(
            _issue(
                "ERR_INVALID_RECIPE_INPUT",
                f"'prompts' supports at most {_SAM3_MAX_PROMPTS} entries.",
            )
        )
    for prompt in prompts:
        if normalize_manga_mask_category(prompt) is None:
            errors.append(
                _issue(
                    "ERR_INVALID_RECIPE_INPUT",
                    f"'prompts' entry cannot be mapped to a manga mask category: {prompt}",
                )
            )
    return prompts


def _validation_result(
    valid: bool,
    *,
    warnings: Sequence[Mapping[str, Any]] | None = None,
    errors: Sequence[Mapping[str, Any]] | None = None,
    normalized_input: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "valid": valid,
        "warnings": list(warnings or ()),
        "errors": list(errors or ()),
        "normalizedInput": dict(normalized_input or {}),
    }


def _capability_check(
    service: Any,
    requires_external: bool,
    file_write: bool,
    *,
    sam3_recipe: bool = False,
) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    if requires_external and not service.safety.external_services_allowed:
        missing.append("privacy/allow_external_services")
    if requires_external:
        missing.extend(production_generation_blocks(service))
    if file_write and not service.safety.roots:
        missing.append("mcp_roots")
    if service.safety.confirmation_policy == "always_allow":
        warnings.append("confirmation_policy_always_allow_is_not_recommended_for_production")
    # Warning, not missing: dry_run itself succeeds without a provider, but an
    # unwired environment should learn at plan time that submit will fail.
    if sam3_recipe and getattr(service, "sam3_provider", None) is None:
        warnings.append("sam3_provider_missing")
    return {"passed": not missing, "warnings": warnings, "missing": missing}


def _planned_steps(
    recipe_id: str,
    domain: str,
    normalized: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidate_count = int(dict(normalized.get("candidatePolicy") or {}).get("candidateCount") or 1)
    steps = []
    for step in _steps_for_recipe(recipe_id, domain):
        item = {"stepId": step, "sideEffect": step in _side_effect_step_ids()}
        if step in {"generate_candidates", "generate_panel_candidates"}:
            item["candidateCount"] = candidate_count
        steps.append(item)
    return steps


def _steps_for_recipe(recipe_id: str, domain: str) -> list[str]:
    if _sam3_recipe_descriptor_by_id(recipe_id) is not None:
        return _sam3_steps_for_recipe(recipe_id)
    common = [
        "validate_input",
        "inject_bible_rules",
        "generate_candidates",
        "import_generated_assets",
        "run_domain_qa",
        "score_candidates",
        "create_rework_plan",
        "export_package",
        "record_lineage",
    ]
    if domain == "comic_page":
        return [
            "validate_input",
            "inject_bible_rules",
            "validate_comic_plan",
            "build_safe_area_map",
            "generate_panel_candidates",
            "score_candidates",
            "select_best_candidates",
            "create_rework_plan",
            "apply_speech_bubbles",
            "render_preview",
            "run_visual_qa",
            "validate_export_profile",
            "export_package",
            "record_lineage",
        ]
    if domain == "game_asset_3d":
        return [
            "validate_input",
            "inject_bible_rules",
            "generate_candidates" if recipe_id.endswith(".create") else "import_source_asset",
            "import_generated_assets",
            "render_3d_previews",
            "run_domain_qa",
            "score_candidates",
            "create_rework_plan",
            "validate_export_profile",
            "export_package",
            "record_lineage",
        ]
    return common


def _sam3_steps_for_recipe(recipe_id: str) -> list[str]:
    common = [
        "validate_input",
        "check_sam3_readiness",
        "resolve_source_snapshot",
    ]
    specific = {
        "sam3_extract_masks": [
            "plan_mask_candidates",
            "score_mask_candidates",
            "project_scorecard",
        ],
        "sam3_split_character_layers": [
            "plan_character_layer_masks",
            "score_mask_candidates",
            "project_scorecard",
        ],
        "sam3_prepare_inpaint_package": [
            "plan_inpaint_mask_package",
            "score_mask_candidates",
            "project_scorecard",
        ],
        "sam3_detect_speech_bubbles": [
            "plan_speech_bubble_masks",
            "score_mask_candidates",
            "project_scorecard",
        ],
    }
    return [*common, *specific.get(recipe_id, ()), "record_lineage"]


def _side_effect_step_ids() -> set[str]:
    return {
        "generate_candidates",
        "generate_panel_candidates",
        "import_generated_assets",
        "apply_speech_bubbles",
        "render_preview",
        "render_3d_previews",
        "export_package",
        "record_lineage",
    }


def _side_effects_for_recipe(recipe_id: str) -> list[str]:
    sam3_descriptor = _sam3_recipe_descriptor_by_id(recipe_id)
    if sam3_descriptor is not None:
        return list(sam3_descriptor.side_effects)
    recipe = _recipe_by_id(recipe_id)
    effects = ["asset_registration", "production_metadata_write"]
    if recipe and recipe.get("requiresExternalServices"):
        effects.insert(0, "external_generation")
    file_write_prefixes = (
        "comic_page",
        "character",
        "game_asset_3d",
        "sprite",
        "texture",
        "background",
        "item",
    )
    if recipe_id.startswith(file_write_prefixes):
        effects.append("file_write")
    return effects


def _credentials_for_recipe(recipe_id: str) -> list[str]:
    if recipe_id.startswith("game_asset_3d"):
        return ["generation_gateway", "3d_generation_backend"]
    return ["generation_gateway"]


def _estimated_artifacts_for_recipe(recipe_id: str, domain: str) -> list[str]:
    sam3_descriptor = _sam3_recipe_descriptor_by_id(recipe_id)
    if sam3_descriptor is not None:
        return list(sam3_descriptor.estimated_artifacts)
    return _estimated_artifacts(domain)


def _estimated_artifacts(domain: str) -> list[str]:
    if domain == "comic_page":
        return [
            "candidate_preview_png",
            "candidate_scorecard_json",
            "final_page_png",
            "qa_report_json",
            "lineage_json",
        ]
    if domain == "character_sheet":
        return [
            "character_sheet_preview_png",
            "front_view_png",
            "side_view_png",
            "back_view_png",
            "qa_report_json",
            "lineage_json",
        ]
    if domain == "game_asset_3d":
        return [
            "candidate_glb",
            "candidate_preview_contact_sheet_png",
            "candidate_scorecard_json",
            "final_glb",
            "final_fbx",
            "texture_png",
            "qa_report_json",
        ]
    return ["candidate_preview_png", "candidate_scorecard_json", "qa_report_json", "lineage_json"]


def _missing_confirmation(plan: Mapping[str, Any], confirmation: Mapping[str, Any]) -> list[str]:
    missing = []
    requires = dict(plan.get("requires") or {})
    if requires.get("externalServices") and confirmation.get("allowExternalServices") is not True:
        missing.append("allowExternalServices")
    if requires.get("fileWrite") and confirmation.get("allowFileWrite") is not True:
        missing.append("allowFileWrite")
    if requires.get("liveCanvasMutation") and confirmation.get("allowCanvasMutation") is not True:
        missing.append("allowCanvasMutation")
    return missing


def _runner_confirmation_details(
    plan: Mapping[str, Any],
    confirmation: Mapping[str, Any],
) -> dict[str, Any]:
    return pct.scrub_for_pct(
        {
            "recipeId": plan.get("recipeId"),
            "domain": plan.get("domain"),
            "planId": plan.get("planId"),
            "requires": plan.get("requires"),
            "sideEffects": plan.get("sideEffects"),
            "allowedSideEffects": dict(plan.get("confirmationPayload") or {}).get(
                "allowedSideEffects",
                [],
            ),
            "confirmation": {
                "planId": confirmation.get("planId"),
                "allowExternalServices": confirmation.get("allowExternalServices"),
                "allowFileWrite": confirmation.get("allowFileWrite"),
                "allowCanvasMutation": confirmation.get("allowCanvasMutation"),
            },
        }
    )


def _score_candidate(
    candidate: Mapping[str, Any],
    domain: str,
    quality_profile: str,
    checks: Sequence[str],
) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    hard_failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    check_results = []
    score = 100
    hard_checks = set(dict(_QUALITY_PROFILES.get(quality_profile, {})).get("hardChecks", []))
    for check in checks:
        status, check_score, message = _evaluate_check(check, domain, metadata)
        check_results.append({"check": check, "status": status, "score": check_score})
        if status == "hard_fail":
            hard_failures.append({"check": check, "message": message})
            score -= 35 if check in hard_checks else 20
        elif status == "warning":
            warnings.append({"check": check, "message": message})
            score -= 12
        elif status == "not_checked":
            score -= 3
    score = max(0, min(100, score))
    recommendation = "keep"
    if hard_failures:
        recommendation = "reject"
    elif score < 60:
        recommendation = "rework"
    elif warnings:
        recommendation = "manual_review"
    return {
        "candidateId": str(candidate.get("candidateId") or ""),
        "assetId": str(candidate.get("assetId") or ""),
        "artifactId": str(candidate.get("artifactId") or ""),
        "score": score,
        "decisionRecommendation": recommendation,
        "hardFailures": hard_failures,
        "warnings": warnings,
        "checkResults": check_results,
    }


def _evaluate_check(  # noqa: PLR0911, PLR0912
    check: str,
    domain: str,
    metadata: Mapping[str, Any],
) -> tuple[str, int, str]:
    if not metadata:
        return ("not_checked", 0, "No deterministic metadata was provided for this check.")
    if check in {"panel_resolution", "image_resolution"}:
        width = _as_int(metadata.get("width"))
        height = _as_int(metadata.get("height"))
        min_size = 768
        if min(width, height) >= min_size:
            return ("pass", 100, "")
        return ("hard_fail", 30, f"Minimum dimension is below {min_size}px.")
    if check == "triangle_budget":
        triangles = _as_int(metadata.get("triangles"))
        max_triangles = _as_int(metadata.get("maxTriangles")) or 1500
        if triangles and triangles <= max_triangles:
            return ("pass", 100, "")
        if triangles:
            return ("hard_fail", 20, f"Triangle count exceeds {max_triangles}.")
        return ("hard_fail", 20, "Triangle count is unavailable.")
    if check in {"has_uvs", "has_normals", "file_loadable"}:
        if metadata.get(check) is True:
            return ("pass", 100, "")
        return ("hard_fail", 0, f"{check} is required.")
    if check in {"text_safe_area", "speech_bubble_fit", "bubble_overlap"}:
        if metadata.get(check) is False:
            return ("hard_fail", 0, f"{check} failed.")
        if metadata.get(check) is True:
            return ("pass", 100, "")
        return ("not_checked", 0, "No comic layout metric was provided.")
    if check == "export_profile":
        if metadata.get("exportProfileValid") is False:
            return ("hard_fail", 0, "Export profile validation failed.")
        if metadata.get("exportProfileValid") is True:
            return ("pass", 100, "")
    if domain == "game_asset_3d" and check == "material_count":
        material_count = _as_int(metadata.get("materialCount"))
        max_materials = _as_int(metadata.get("maxMaterials")) or 3
        if material_count <= max_materials:
            return ("pass", 100, "")
        return ("warning", 70, f"Material count exceeds {max_materials}.")
    if domain == "game_asset_3d" and check == "texture_resolution":
        max_observed = _as_int(metadata.get("textureResolutionMax"))
        texture_count = _as_int(metadata.get("textureCount"))
        max_allowed = _as_int(metadata.get("maxTextureSize")) or 1024
        if texture_count == 0 and not max_observed:
            return ("not_checked", 0, "No texture resolution metadata was provided.")
        if max_observed and max_observed <= max_allowed:
            return ("pass", 100, "")
        return ("warning", 70, f"Texture resolution exceeds {max_allowed}px.")
    if domain == "game_asset_3d" and check == "origin_and_scale":
        if isinstance(metadata.get("boundingBox"), Mapping):
            return ("pass", 100, "")
        return ("warning", 70, "Bounds metadata is unavailable.")
    if domain == "game_asset_3d" and check == "export_format":
        source_format = str(metadata.get("sourceFormat") or "").lower()
        if source_format in {"glb", "gltf", "obj"}:
            return ("pass", 100, "")
        if source_format:
            return ("warning", 70, f"Source format {source_format} needs export review.")
        return ("not_checked", 0, "Source format metadata is unavailable.")
    return ("not_checked", 0, "No deterministic implementation for this soft check.")


def _select_candidate(results: Sequence[Mapping[str, Any]]) -> str | None:
    keepable = [
        item
        for item in results
        if item.get("decisionRecommendation") in {"keep", "manual_review", "rework"}
    ]
    if not keepable:
        return None
    selected = max(keepable, key=lambda item: int(item.get("score") or 0))
    return str(selected.get("candidateId") or "") or None


_SAM3_RESELECT_MASK_CHECKS = frozenset(
    {
        "candidate_area_empty",
        "candidate_bbox_outside_source",
    }
)
_SAM3_REFINE_MASK_CHECKS = frozenset(
    {
        "candidate_area_too_small",
        "candidate_area_too_large",
        "candidate_confidence_low",
    }
)
_SAM3_REVIEW_MASK_CHECKS = frozenset(
    {
        "candidate_score_missing",
        "candidate_not_accepted",
    }
)
_SAM3_MASK_REWORK_CHECKS = (
    _SAM3_RESELECT_MASK_CHECKS | _SAM3_REFINE_MASK_CHECKS | _SAM3_REVIEW_MASK_CHECKS
)
_SAM3_MASK_REWORK_CONSTRAINTS = {
    "candidate_area_empty": "Select a non-empty SAM3 mask candidate inside the source bounds.",
    "candidate_bbox_outside_source": (
        "Select a non-empty SAM3 mask candidate inside the source bounds."
    ),
    "candidate_area_too_small": "Expand the SAM3 mask candidate before re-scoring.",
    "candidate_area_too_large": "Contract the SAM3 mask candidate before re-scoring.",
    "candidate_confidence_low": "Use a clearer SAM3 prompt or refine the mask candidate.",
    "candidate_score_missing": "Review the SAM3 candidate metadata before production scoring.",
    "candidate_not_accepted": "Require user acceptance before production keep.",
}


def _prompt_patch_for_failures(domain: str, checks: Sequence[str]) -> dict[str, Any]:
    positive: list[str] = []
    negative: list[str] = []
    constraints: list[str] = []
    has_comic_layout_failure = any("safe_area" in check or "bubble" in check for check in checks)
    if domain == "comic_page" and has_comic_layout_failure:
        positive.append("Keep planned speech bubble areas visually simple and empty.")
        negative.extend(["text", "letters", "logo", "watermark", "face in speech bubble area"])
    if domain == "game_asset_3d":
        positive.append("Create clean real-time game asset topology with UVs and normals.")
        negative.extend(["non-manifold geometry", "excessive materials", "oversized textures"])
    constraints.extend(_sam3_rework_constraints(checks))
    return {
        "positiveAdditions": positive,
        "negativeAdditions": negative,
        "constraints": constraints,
    }


def _layout_patch_for_failures(domain: str, checks: Sequence[str]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    has_comic_layout_failure = any("safe_area" in check or "bubble" in check for check in checks)
    if domain == "comic_page" and has_comic_layout_failure:
        patch["preserveSafeAreas"] = [
            {"x": 0.62, "y": 0.03, "w": 0.35, "h": 0.32},
        ]
    sam3_review = _sam3_candidate_review_patch(checks)
    if sam3_review:
        patch["sam3CandidateReview"] = sam3_review
    return patch


def _sam3_rework_constraints(checks: Sequence[str]) -> list[str]:
    constraints: list[str] = []
    for check in checks:
        constraint = _SAM3_MASK_REWORK_CONSTRAINTS.get(check)
        if constraint and constraint not in constraints:
            constraints.append(constraint)
    return constraints


def _sam3_candidate_review_patch(checks: Sequence[str]) -> dict[str, Any]:
    sam3_checks = _unique_items(check for check in checks if check in _SAM3_MASK_REWORK_CHECKS)
    if not sam3_checks:
        return {}
    required_actions = _unique_items(_sam3_rework_action_for_check(check) for check in sam3_checks)
    return {
        "checks": sam3_checks,
        "requiredActions": required_actions,
        "preserveSourceBounds": any(check in _SAM3_RESELECT_MASK_CHECKS for check in sam3_checks),
    }


def _sam3_rework_action_for_check(check: str) -> str:
    if check in _SAM3_RESELECT_MASK_CHECKS:
        return "reselect_mask_candidate"
    if check in _SAM3_REFINE_MASK_CHECKS:
        return "refine_mask_candidate"
    if check == "candidate_score_missing":
        return "review_candidate"
    if check == "candidate_not_accepted":
        return "accept_candidate"
    return "review_candidate"


def _unique_items(values: Sequence[str] | Any) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in unique:
            unique.append(text)
    return unique


def _package_artifacts(
    job: Mapping[str, Any],
    domain: str,
    export_profile: str,
) -> list[dict[str, Any]]:
    sam3_kinds = _sam3_export_artifact_kinds(job, export_profile)
    if sam3_kinds is not None:
        kinds = list(sam3_kinds)
    elif domain == "comic_page":
        kinds = ["final_page_png", "qa_report_json", "candidate_scorecard_json", "lineage_json"]
    elif domain == "character_sheet":
        kinds = ["character_sheet_png", "character_rules_json", "qa_report_json", "lineage_json"]
    elif domain == "game_asset_3d":
        kinds = [
            "final_glb",
            "final_fbx",
            "preview_contact_sheet_png",
            "asset_metrics_json",
            "lineage_json",
        ]
    else:
        kinds = ["final_preview_png", "qa_report_json", "lineage_json"]
    return [
        {
            "kind": kind,
            "assetId": _stable_id(
                "asset",
                {"domain": domain, "profile": export_profile, "kind": kind},
            ),
        }
        for kind in kinds
    ]


def _sam3_export_artifact_kinds(
    job: Mapping[str, Any],
    export_profile: str,
) -> tuple[str, ...] | None:
    if not _is_sam3_export_profile(export_profile):
        return None
    descriptor = _sam3_recipe_descriptor_by_id(str(job.get("recipeId") or ""))
    if descriptor is not None and export_profile in descriptor.export_profiles:
        return tuple(descriptor.estimated_artifacts)
    return _SAM3_EXPORT_PROFILE_ARTIFACTS.get(export_profile)


def _is_sam3_export_profile(export_profile: str) -> bool:
    if export_profile in _SAM3_EXPORT_PROFILE_ARTIFACTS:
        return True
    return any(
        export_profile in descriptor.export_profiles
        for descriptor in sam3_production_recipe_descriptors()
    )


def _sam3_package_manifest(
    job: Mapping[str, Any],
    export_profile: str,
    artifacts: Sequence[Mapping[str, Any]],
    source_artifacts: Sequence[Mapping[str, Any]],
    package: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _sam3_export_artifact_kinds(job, export_profile) is None:
        return None
    artifact_kinds = [
        str(artifact.get("kind") or "") for artifact in artifacts if str(artifact.get("kind") or "")
    ]
    source_artifact_ids = [
        str(artifact.get("artifactId") or "")
        for artifact in source_artifacts
        if str(artifact.get("artifactId") or "")
    ]
    return {
        "schemaVersion": _SAM3_PACKAGE_MANIFEST_SCHEMA_VERSION,
        "manifestKind": export_profile,
        "sourceJobId": str(job.get("jobId") or ""),
        "recipeId": str(job.get("recipeId") or ""),
        "profile": export_profile,
        "artifactKinds": artifact_kinds,
        "sourceArtifactIds": source_artifact_ids,
        "scorecardIds": list(package.get("scorecardIds") or []),
        "lineageEdgeIds": list(package.get("lineageEdgeIds") or []),
        "includeIntermediateArtifacts": bool(package.get("includeIntermediateArtifacts", False)),
        "includeLineage": bool(package.get("includeLineage", True)),
        "includeQaReports": bool(package.get("includeQaReports", True)),
        "includeRejectedCandidates": bool(package.get("includeRejectedCandidates", False)),
    }


def _sam3_production_control_projection(
    job: Mapping[str, Any],
    packages: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    sam3_packages = [
        dict(package)
        for package in packages
        if isinstance(package.get("sam3PackageManifest"), Mapping)
    ]
    if not sam3_packages:
        return None
    manifests = [dict(package.get("sam3PackageManifest") or {}) for package in sam3_packages]
    job_id = str(job.get("jobId") or "")
    recipe_id = str(job.get("recipeId") or "") or next(
        (
            str(manifest.get("recipeId") or "")
            for manifest in manifests
            if str(manifest.get("recipeId") or "")
        ),
        "",
    )
    package_ids = _unique_items(str(package.get("packageId") or "") for package in sam3_packages)
    package_kinds = _unique_items(
        [
            *(
                str(package.get("packageKind") or "")
                for package in sam3_packages
                if str(package.get("packageKind") or "")
            ),
            *(
                str(manifest.get("manifestKind") or manifest.get("profile") or "")
                for manifest in manifests
                if str(manifest.get("manifestKind") or manifest.get("profile") or "")
            ),
        ]
    )
    source_artifact_ids = _unique_items(
        source_id
        for manifest in manifests
        for source_id in _manifest_string_list(manifest.get("sourceArtifactIds"))
    )
    scorecard_ids = _unique_items(
        scorecard_id
        for manifest in manifests
        for scorecard_id in _manifest_string_list(manifest.get("scorecardIds"))
    )
    lineage_edge_ids = _unique_items(
        edge_id
        for manifest in manifests
        for edge_id in _manifest_string_list(manifest.get("lineageEdgeIds"))
    )
    return {
        "schemaVersion": _SAM3_PRODUCTION_CONTROL_SCHEMA_VERSION,
        "jobId": job_id,
        "recipeId": recipe_id,
        "status": str(job.get("status") or ""),
        "readOnly": True,
        "packageIds": package_ids,
        "packageKinds": package_kinds,
        "sourceArtifactIds": source_artifact_ids,
        "scorecardIds": scorecard_ids,
        "lineageEdgeIds": lineage_edge_ids,
        "tabs": [
            {"tabId": "artifacts", "recordIds": package_ids},
            {"tabId": "jobs", "recordIds": _unique_items([job_id])},
            {"tabId": "visual_qa", "recordIds": scorecard_ids},
            {"tabId": "recipes", "recordIds": _unique_items([recipe_id])},
            {"tabId": "lineage", "recordIds": lineage_edge_ids},
        ],
        "summary": {
            "packageCount": len(package_ids),
            "sourceArtifactCount": len(source_artifact_ids),
            "scorecardCount": len(scorecard_ids),
            "lineageEdgeCount": len(lineage_edge_ids),
        },
    }


def _sam3_candidate_package_summaries(
    packages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for package in packages:
        manifest = package.get("sam3PackageManifest")
        if not isinstance(manifest, Mapping):
            continue
        manifest_payload = dict(manifest)
        package_kind = str(
            package.get("packageKind")
            or manifest_payload.get("manifestKind")
            or manifest_payload.get("profile")
            or ""
        )
        summaries.append(
            {
                "packageId": str(package.get("packageId") or ""),
                "packageKind": package_kind,
                "profile": str(
                    manifest_payload.get("profile")
                    or manifest_payload.get("manifestKind")
                    or package_kind
                ),
                "artifactKinds": _manifest_string_list(manifest_payload.get("artifactKinds")),
                "sourceArtifactIds": _manifest_string_list(
                    manifest_payload.get("sourceArtifactIds")
                ),
                "scorecardIds": _manifest_string_list(manifest_payload.get("scorecardIds")),
                "lineageEdgeIds": _manifest_string_list(manifest_payload.get("lineageEdgeIds")),
            }
        )
    return summaries


def _sam3_packages_for_source_artifact(
    store: Any,
    artifact_id: str,
) -> list[Mapping[str, Any]]:
    packages: list[Mapping[str, Any]] = []
    for package in store.read_records("packages"):
        manifest = package.get("sam3PackageManifest")
        if not isinstance(manifest, Mapping):
            continue
        source_artifact_ids = _manifest_string_list(manifest.get("sourceArtifactIds"))
        if artifact_id in source_artifact_ids:
            packages.append(package)
    return packages


def _sam3_artifact_mask_package_summaries(
    packages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for package in packages:
        manifest = package.get("sam3PackageManifest")
        if not isinstance(manifest, Mapping):
            continue
        manifest_payload = dict(manifest)
        package_kind = str(
            package.get("packageKind")
            or manifest_payload.get("manifestKind")
            or manifest_payload.get("profile")
            or ""
        )
        job_id = str(package.get("jobId") or "")
        summary = {
            "packageId": str(package.get("packageId") or ""),
            "packageKind": package_kind,
            "profile": str(
                manifest_payload.get("profile")
                or manifest_payload.get("manifestKind")
                or package_kind
            ),
            "jobId": job_id,
            "artifactKinds": _manifest_string_list(manifest_payload.get("artifactKinds")),
            "sourceArtifactIds": _manifest_string_list(manifest_payload.get("sourceArtifactIds")),
            "scorecardIds": _manifest_string_list(manifest_payload.get("scorecardIds")),
            "lineageEdgeIds": _manifest_string_list(manifest_payload.get("lineageEdgeIds")),
            "relatedResources": {},
        }
        if job_id:
            quoted_job_id = quote(job_id, safe="")
            summary["relatedResources"] = {
                "job": f"loomstroke://production/job/{quoted_job_id}",
                "jobSam3Candidates": (
                    f"loomstroke://production/job/{quoted_job_id}/sam3-candidates"
                ),
                "jobExports": f"loomstroke://production/job/{quoted_job_id}/exports",
                "lineage": f"loomstroke://production/job/{quoted_job_id}/lineage",
                "scorecard": f"loomstroke://production/job/{quoted_job_id}/scorecard",
            }
        summaries.append(summary)
    return summaries


def _sam3_lineage_projection(
    job: Mapping[str, Any],
    edges: Sequence[Mapping[str, Any]],
    source_artifacts: Sequence[Mapping[str, Any]],
    scorecards: Sequence[Mapping[str, Any]],
    packages: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    sam3_packages = [
        dict(package)
        for package in packages
        if isinstance(package.get("sam3PackageManifest"), Mapping)
    ]
    if not sam3_packages:
        return None
    manifests = [dict(package.get("sam3PackageManifest") or {}) for package in sam3_packages]
    package_ids = _unique_items(str(package.get("packageId") or "") for package in sam3_packages)
    source_artifact_ids = _unique_items(
        [
            *(
                str(artifact.get("artifactId") or "")
                for artifact in source_artifacts
                if str(artifact.get("artifactId") or "")
            ),
            *(
                source_id
                for manifest in manifests
                for source_id in _manifest_string_list(manifest.get("sourceArtifactIds"))
            ),
        ]
    )
    scorecard_ids = _unique_items(
        [
            *(
                str(card.get("scorecardId") or "")
                for card in scorecards
                if str(card.get("scorecardId") or "")
            ),
            *(
                scorecard_id
                for manifest in manifests
                for scorecard_id in _manifest_string_list(manifest.get("scorecardIds"))
            ),
        ]
    )
    lineage_edge_ids = _unique_items(
        [
            *(str(edge.get("edgeId") or "") for edge in edges if str(edge.get("edgeId") or "")),
            *(
                edge_id
                for manifest in manifests
                for edge_id in _manifest_string_list(manifest.get("lineageEdgeIds"))
            ),
        ]
    )
    return {
        "schemaVersion": _SAM3_LINEAGE_PROJECTION_SCHEMA_VERSION,
        "jobId": str(job.get("jobId") or ""),
        "recipeId": str(job.get("recipeId") or ""),
        "packageIds": package_ids,
        "sourceArtifactIds": source_artifact_ids,
        "scorecardIds": scorecard_ids,
        "lineageEdgeIds": lineage_edge_ids,
        "chains": [
            {"step": "source_snapshot", "recordIds": source_artifact_ids},
            {"step": "candidate_scorecard", "recordIds": scorecard_ids},
            {"step": "package_manifest", "recordIds": package_ids},
            {"step": "lineage_edges", "recordIds": lineage_edge_ids},
        ],
        "packageEdges": [
            {
                "edgeId": _stable_id(
                    "sam3_pkg_edge",
                    {"sourceArtifactId": source_id, "packageId": package_id},
                ),
                "fromNodeId": source_id,
                "toNodeId": package_id,
                "edgeKind": "packaged_into_sam3_export",
            }
            for package_id in package_ids
            for source_id in source_artifact_ids
        ],
    }


def _manifest_string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item or "")]


def _quality_profile_checks(profile_id: str) -> list[str]:
    profile = _QUALITY_PROFILES.get(profile_id)
    if not profile:
        return []
    return list(profile["hardChecks"]) + list(profile["softChecks"])


def _input_schema_for_domain(domain: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["target", "brief", "qualityProfile"],
        "properties": {
            "target": {"type": "object", "additionalProperties": True},
            "brief": {"type": "object", "additionalProperties": True},
            "constraints": {"type": "object", "additionalProperties": True},
            "qualityProfile": {"type": "string"},
            "candidatePolicy": {"type": "object", "additionalProperties": True},
            "exportProfile": {"type": "string"},
        },
        "additionalProperties": True,
        "domain": domain,
    }


def _default_candidate_policy(quality_profile: str) -> dict[str, int]:
    profile = _QUALITY_PROFILES.get(quality_profile)
    if profile:
        return dict(profile["defaultCandidatePolicy"])
    return {"candidateCount": 4, "maxReworkAttempts": 2}


def _default_export_profile(recipe: Mapping[str, Any]) -> str:
    profiles = recipe.get("supportedExportProfiles") or []
    return str(profiles[0]) if profiles else "asset_package"


def _required_string(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            f"'{key}' is required.",
            details={"argument": key},
            code=-32602,
        )
    return value.strip()


def _issue(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


def _error(code: str, message: str, *, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": RUNNER_SCHEMA_VERSION,
        "error": {
            "code": code,
            "message": message,
            "details": dict(details or {}),
        },
    }


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(pct.scrub_for_pct(dict(payload)), sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        coerced = default
    return max(minimum, min(maximum, coerced))


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rework_reason(checks: Sequence[str]) -> str:
    filtered = [check for check in checks if check]
    if not filtered:
        return "No deterministic failure was provided; manual review is recommended."
    return "Rework recommended for: " + ", ".join(filtered)
