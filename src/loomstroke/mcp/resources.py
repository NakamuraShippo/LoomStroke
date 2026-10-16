"""MCP resources/list and resources/read handlers."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, unquote

from .comic_agent_guide import comic_agent_guide
from .serialization import dumps_json
from .types import McpResourceSpec, McpStructuredError, ReasonCode


def list_resources(service: Any) -> dict[str, Any]:  # noqa: PLR0912
    resources: list[McpResourceSpec] = [
        McpResourceSpec(
            uri="loomstroke://capabilities",
            name="LoomStroke MCP capabilities",
            description="Capability, guard, version, and environment report.",
        ),
        McpResourceSpec(
            uri="loomstroke://diagnostics/readiness",
            name="LoomStroke MCP readiness diagnostics",
            description="Connection, guard, tool readiness, and client setup diagnostics.",
        ),
        McpResourceSpec(
            uri="loomstroke://agent-guide",
            name="LoomStroke MCP agent guide",
            description="Client-AI guide for creation requests, including the comic workflow.",
        ),
        McpResourceSpec(
            uri="loomstroke://agent/context/current",
            name="Current Canvas Agent edit context",
            description="Secret-free read-only edit context for Canvas Agent Phase 0.",
        ),
        McpResourceSpec(
            uri="loomstroke://render/scene/current",
            name="Current render scene",
            description="Current RenderSceneSnapshot JSON representation.",
        ),
        McpResourceSpec(
            uri="loomstroke://production/capabilities",
            name="Production Control Tower capabilities",
            description="Production catalog, store, guards, tools, resources, and safety notes.",
        ),
        McpResourceSpec(
            uri="loomstroke://production/artifacts",
            name="Production artifacts",
            description="ProductionArtifact list from GenerationHistory and PCT metadata.",
        ),
        McpResourceSpec(
            uri="loomstroke://production/jobs",
            name="Production jobs",
            description="Unified view of MCP jobs and persisted ProductionJob records.",
        ),
        McpResourceSpec(
            uri="loomstroke://production/bible/project",
            name="Production Bible project scope",
            description="Project-level Production Bible rules.",
        ),
        McpResourceSpec(
            uri="loomstroke://production/export-profiles",
            name="Production export profiles",
            description="Stored export profile validation metadata.",
        ),
        McpResourceSpec(
            uri="loomstroke://production/recipes",
            name="ProductionRecipe runner catalog",
            description="Canonical ProductionRecipe runner catalog.",
        ),
        McpResourceSpec(
            uri="loomstroke://production/quality-profiles",
            name="ProductionRecipe quality profiles",
            description="Canonical ProductionRecipe QualityProfile catalog.",
        ),
        McpResourceSpec(
            uri="loomstroke://ai-effects/catalog",
            name="AI Effect catalog",
            description="Credential-free AI Effect preset catalog.",
        ),
    ]
    for resource in service.list_sam3_resources():
        resources.append(_sam3_resource_spec(resource))
    for document in service.list_documents():
        document_id = str(document.get("document_id") or document.get("documentId") or "")
        if document.get("source") == "live":
            resources.append(
                McpResourceSpec(
                    uri="loomstroke://document/current",
                    name="Current document",
                    description="Live app current document summary.",
                )
            )
        if document_id:
            resources.append(
                McpResourceSpec(
                    uri=f"loomstroke://document/{quote(document_id, safe='')}",
                    name=f"Document {document_id}",
                    description="Document metadata and page summary.",
                )
            )
    for page in service.list_pages():
        page_id = str(page.get("pageId") or page.get("page_id") or "")
        if page_id:
            resources.append(
                McpResourceSpec(
                    uri=f"loomstroke://page/{quote(page_id, safe='')}",
                    name=f"Page {page_id}",
                    description="Page summary, dimensions, and layer order.",
                )
            )
    for layer in service.list_layers():
        layer_id = str(layer.get("layer_id") or layer.get("layerId") or "")
        if layer_id:
            resources.append(
                McpResourceSpec(
                    uri=f"loomstroke://layer/{quote(layer_id, safe='')}",
                    name=f"Layer {layer_id}",
                    description="Layer metadata, type, visibility, and texture version.",
                )
            )
    for asset in service.list_assets():
        asset_id = str(asset.get("assetId") or "")
        if asset_id:
            resources.append(
                McpResourceSpec(
                    uri=f"loomstroke://asset/{quote(asset_id, safe='')}",
                    name=f"Asset {asset_id}",
                    description="Registered asset metadata.",
                )
            )
    for job in service.list_jobs():
        job_id = str(job.get("jobId") or "")
        if job_id:
            resources.append(
                McpResourceSpec(
                    uri=f"loomstroke://job/{quote(job_id, safe='')}",
                    name=f"Job {job_id}",
                    description="Generation or render job status.",
                )
            )
    for run in service.list_canvas_agent_runs():
        run_id = str(run.get("runId") or "")
        if run_id:
            quoted = quote(run_id, safe="")
            resources.append(
                McpResourceSpec(
                    uri=f"loomstroke://agent/run/{quoted}",
                    name=f"Canvas Agent run {run_id}",
                    description="Canvas Agent read-only run state.",
                )
            )
            if run.get("planId"):
                resources.append(
                    McpResourceSpec(
                        uri=f"loomstroke://agent/run/{quoted}/plan",
                        name=f"Canvas Agent run {run_id} plan",
                        description="Canvas Agent read-only plan.",
                    )
                )
            if run.get("clarificationId"):
                resources.append(
                    McpResourceSpec(
                        uri=f"loomstroke://agent/run/{quoted}/clarification",
                        name=f"Canvas Agent run {run_id} clarification",
                        description="Canvas Agent actionable clarification.",
                    )
                )
            if run.get("clarificationAnswer"):
                resources.append(
                    McpResourceSpec(
                        uri=f"loomstroke://agent/run/{quoted}/clarification-answer",
                        name=f"Canvas Agent run {run_id} clarification answer",
                        description="Canvas Agent clarification answer result.",
                    )
                )
            if run.get("changesetId"):
                resources.append(
                    McpResourceSpec(
                        uri=f"loomstroke://agent/run/{quoted}/changeset",
                        name=f"Canvas Agent run {run_id} changeset",
                        description="Canvas Agent CanvasChangeset draft.",
                    )
                )
            if run.get("previewId"):
                resources.append(
                    McpResourceSpec(
                        uri=f"loomstroke://agent/run/{quoted}/preview",
                        name=f"Canvas Agent run {run_id} preview",
                        description="Canvas Agent non-mutating changeset preview.",
                    )
                )
            if run.get("applyId"):
                resources.append(
                    McpResourceSpec(
                        uri=f"loomstroke://agent/run/{quoted}/apply-result",
                        name=f"Canvas Agent run {run_id} apply result",
                        description="Canvas Agent Command-backed apply result.",
                    )
                )
    for recipe in service.list_production_recipes({"domain": "all"}).get("recipes", []):
        recipe_id = str(recipe.get("recipeId") or "")
        if recipe_id:
            resources.append(
                McpResourceSpec(
                    uri=f"loomstroke://production/recipe/{quote(recipe_id, safe='')}",
                    name=f"ProductionRecipe {recipe_id}",
                    description="Canonical ProductionRecipe runner detail.",
                )
            )
    for profile in service.production_quality_profiles_resource().get("qualityProfiles", []):
        profile_id = str(profile.get("qualityProfileId") or "")
        if profile_id:
            resources.append(
                McpResourceSpec(
                    uri=f"loomstroke://production/quality-profile/{quote(profile_id, safe='')}",
                    name=f"Production QualityProfile {profile_id}",
                    description="Canonical ProductionRecipe QualityProfile detail.",
                )
            )
    production = service.production_artifacts_resource()
    for artifact in production.get("artifacts", []):
        artifact_id = str(artifact.get("artifactId") or "")
        if artifact_id:
            quoted = quote(artifact_id, safe="")
            resources.extend(
                [
                    McpResourceSpec(
                        uri=f"loomstroke://production/artifact/{quoted}",
                        name=f"Production artifact {artifact_id}",
                        description="ProductionArtifact metadata.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/lineage/{quoted}",
                        name=f"Production lineage {artifact_id}",
                        description="Incoming and outgoing ProductionEdge metadata.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/artifact/{quoted}/masks",
                        name=f"Production artifact {artifact_id} SAM3 masks",
                        description=(
                            f"Production artifact {artifact_id} SAM3 mask package summary."
                        ),
                    ),
                ]
            )
    for job in service.list_production_jobs_resource().get("jobs", []):
        job_id = str(job.get("jobId") or "")
        if job_id:
            quoted = quote(job_id, safe="")
            resources.extend(
                [
                    McpResourceSpec(
                        uri=f"loomstroke://production/job/{quoted}",
                        name=f"Production job {job_id}",
                        description="ProductionJob or MCP job status.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/job/{quoted}/plan",
                        name=f"Production job {job_id} plan",
                        description="ProductionRecipe dry-run plan captured at submit time.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/job/{quoted}/candidates",
                        name=f"Production job {job_id} candidates",
                        description="ProductionRecipe candidate artifacts.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/job/{quoted}/sam3-candidates",
                        name=f"Production job {job_id} SAM3 candidates",
                        description=f"Production job {job_id} SAM3 candidate packages.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/job/{quoted}/scorecard",
                        name=f"Production job {job_id} scorecard",
                        description="ProductionRecipe candidate scorecards.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/job/{quoted}/rework",
                        name=f"Production job {job_id} rework",
                        description="ProductionRecipe rework plans.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/job/{quoted}/exports",
                        name=f"Production job {job_id} exports",
                        description="ProductionRecipe export package metadata.",
                    ),
                    McpResourceSpec(
                        uri=f"loomstroke://production/job/{quoted}/lineage",
                        name=f"Production job {job_id} lineage",
                        description="ProductionRecipe lineage edges for this job.",
                    ),
                ]
            )
    return {"resources": [resource.to_protocol() for resource in resources]}


def read_resource(service: Any, uri: str) -> dict[str, Any]:  # noqa: PLR0911, PLR0912, PLR0915
    if uri == "loomstroke://capabilities":
        return _json_resource(uri, service.get_capabilities())
    if uri == "loomstroke://diagnostics/readiness":
        return _json_resource(uri, service.get_readiness())
    if uri == "loomstroke://agent-guide":
        return _json_resource(uri, _agent_guide_resource())
    if uri == "loomstroke://agent/context/current":
        return _json_resource(uri, service.canvas_agent_context_resource())
    if uri == "loomstroke://document/current":
        return _json_resource(uri, service.get_document("current"))
    if uri == "loomstroke://render/scene/current":
        return _json_resource(uri, service.get_scene_snapshot())
    if uri == "loomstroke://production/capabilities":
        return _json_resource(uri, service.get_production_capabilities())
    if uri == "loomstroke://production/artifacts":
        return _json_resource(uri, service.production_artifacts_resource())
    if uri == "loomstroke://production/jobs":
        return _json_resource(uri, service.list_production_jobs_resource())
    if uri == "loomstroke://production/export-profiles":
        return _json_resource(uri, service.list_export_profiles_resource())
    if uri == "loomstroke://production/recipes":
        return _json_resource(uri, service.list_production_recipes({"domain": "all"}))
    if uri == "loomstroke://production/quality-profiles":
        return _json_resource(uri, service.production_quality_profiles_resource())
    if uri == "loomstroke://ai-effects/catalog":
        return _json_resource(uri, service.ai_effect_catalog_resource())
    if uri.startswith("loomstroke://ai-effects/preset/"):
        preset_id = unquote(uri.removeprefix("loomstroke://ai-effects/preset/"))
        return _json_resource(uri, service.ai_effect_preset_resource(preset_id))
    if uri.startswith("loomstroke://sam3/"):
        return _json_resource(uri, service.read_sam3_resource(uri))
    if uri.startswith("loomstroke://agent/run/"):
        tail = uri.removeprefix("loomstroke://agent/run/")
        if tail.endswith("/plan"):
            run_id = unquote(tail.removesuffix("/plan"))
            return _json_resource(uri, service.get_canvas_agent_run_plan(run_id))
        if tail.endswith("/clarification"):
            run_id = unquote(tail.removesuffix("/clarification"))
            return _json_resource(uri, service.get_canvas_agent_run_clarification(run_id))
        if tail.endswith("/clarification-answer"):
            run_id = unquote(tail.removesuffix("/clarification-answer"))
            return _json_resource(uri, service.get_canvas_agent_run_clarification_answer(run_id))
        if tail.endswith("/changeset"):
            run_id = unquote(tail.removesuffix("/changeset"))
            return _json_resource(uri, service.get_canvas_agent_run_changeset(run_id))
        if tail.endswith("/preview"):
            run_id = unquote(tail.removesuffix("/preview"))
            return _json_resource(uri, service.get_canvas_agent_run_preview(run_id))
        if tail.endswith("/apply-result"):
            run_id = unquote(tail.removesuffix("/apply-result"))
            return _json_resource(uri, service.get_canvas_agent_run_apply_result(run_id))
        run_id = unquote(tail)
        return _json_resource(uri, service.get_canvas_agent_run(run_id))
    if uri.startswith("loomstroke://production/artifact/"):
        tail = uri.removeprefix("loomstroke://production/artifact/")
        if tail.endswith("/masks"):
            artifact_id = unquote(tail.removesuffix("/masks"))
            return _json_resource(uri, service.production_artifact_masks_resource(artifact_id))
        artifact_id = unquote(tail)
        return _json_resource(uri, service.get_production_artifact(artifact_id))
    if uri.startswith("loomstroke://production/lineage/"):
        artifact_id = unquote(uri.removeprefix("loomstroke://production/lineage/"))
        return _json_resource(uri, service.get_production_lineage_resource(artifact_id))
    if uri.startswith("loomstroke://production/job/"):
        tail = uri.removeprefix("loomstroke://production/job/")
        if tail.endswith("/plan"):
            job_id = unquote(tail.removesuffix("/plan"))
            return _json_resource(uri, service.production_job_plan_resource(job_id))
        if tail.endswith("/candidates"):
            job_id = unquote(tail.removesuffix("/candidates"))
            return _json_resource(uri, service.production_job_candidates_resource(job_id))
        if tail.endswith("/sam3-candidates"):
            job_id = unquote(tail.removesuffix("/sam3-candidates"))
            return _json_resource(uri, service.production_job_sam3_candidates_resource(job_id))
        if tail.endswith("/scorecard"):
            job_id = unquote(tail.removesuffix("/scorecard"))
            return _json_resource(uri, service.production_job_scorecard_resource(job_id))
        if tail.endswith("/rework"):
            job_id = unquote(tail.removesuffix("/rework"))
            return _json_resource(uri, service.production_job_rework_resource(job_id))
        if tail.endswith("/exports"):
            job_id = unquote(tail.removesuffix("/exports"))
            return _json_resource(uri, service.production_job_exports_resource(job_id))
        if tail.endswith("/lineage"):
            job_id = unquote(tail.removesuffix("/lineage"))
            return _json_resource(uri, service.production_job_lineage_resource(job_id))
        job_id = unquote(tail)
        return _json_resource(uri, service.get_production_job_resource(job_id))
    if uri.startswith("loomstroke://production/bible/"):
        tail = uri.removeprefix("loomstroke://production/bible/")
        parts = [unquote(part) for part in tail.split("/") if part]
        scope = parts[0] if parts else "project"
        scope_id = parts[1] if len(parts) > 1 else None
        return _json_resource(uri, service.get_bible_resource(scope, scope_id))
    if uri.startswith("loomstroke://production/qa/"):
        artifact_id = unquote(uri.removeprefix("loomstroke://production/qa/"))
        return _json_resource(uri, service.get_qa_resource(artifact_id))
    if uri.startswith("loomstroke://production/recipe/"):
        recipe_id = unquote(uri.removeprefix("loomstroke://production/recipe/"))
        detail = service.get_production_recipe_detail({"recipeId": recipe_id})
        if "error" not in detail:
            return _json_resource(uri, detail)
        return _json_resource(uri, service.get_recipe_resource(recipe_id))
    if uri.startswith("loomstroke://production/quality-profile/"):
        profile_id = unquote(uri.removeprefix("loomstroke://production/quality-profile/"))
        return _json_resource(uri, service.production_quality_profile_resource(profile_id))
    if uri.startswith("loomstroke://document/"):
        document_id = unquote(uri.removeprefix("loomstroke://document/"))
        return _json_resource(uri, service.get_document(document_id))
    if uri.startswith("loomstroke://page/"):
        page_id = unquote(uri.removeprefix("loomstroke://page/"))
        return _json_resource(uri, service.get_page(page_id))
    if uri.startswith("loomstroke://layer/"):
        layer_id = unquote(uri.removeprefix("loomstroke://layer/"))
        return _json_resource(uri, service.get_layer(layer_id))
    if uri.startswith("loomstroke://asset/"):
        asset_id = unquote(uri.removeprefix("loomstroke://asset/"))
        return _json_resource(uri, service.get_asset(asset_id))
    if uri.startswith("loomstroke://job/"):
        job_id = unquote(uri.removeprefix("loomstroke://job/"))
        return _json_resource(uri, service.get_job_status(job_id))
    raise McpStructuredError(
        ReasonCode.NOT_FOUND,
        "Resource not found.",
        details={"uri": uri},
        code=-32602,
    )


def _json_resource(uri: str, value: Any) -> dict[str, Any]:
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": dumps_json(value),
            }
        ]
    }


def _sam3_resource_spec(resource: dict[str, Any]) -> McpResourceSpec:
    uri = str(resource.get("uri") or "")
    name = str(resource.get("name") or uri)
    kind = str(resource.get("kind") or "resource").replace("_", " ")
    description = str(resource.get("description") or f"SAM3 {kind} read-only resource.")
    return McpResourceSpec(uri=uri, name=name, description=description)


def _agent_guide_resource() -> dict[str, Any]:
    return {
        "schemaVersion": "mcp.agent_guide.v1",
        "preferredCreationTool": "loomstroke_create",
        "paintingWorkflow": {
            "availability": "Read capabilities.readiness.editing before editing.",
            "document": [
                "loomstroke_new_document",
                "loomstroke_open_document",
                "loomstroke_save_document",
                "loomstroke_close_document",
            ],
            "paint": [
                "loomstroke_draw_stroke",
                "loomstroke_fill",
                "loomstroke_draw_gradient",
                "loomstroke_fill_pattern",
            ],
            "history": ["loomstroke_get_history_state", "loomstroke_undo", "loomstroke_redo"],
            "guard": (
                "Use explicit layer IDs and settings, optional expectedRevision from "
                "get_edit_context, and the configured MCP confirmation policy. Save before "
                "closing or explicitly discardChanges. Document files require MCP roots."
            ),
        },
        "naturalLanguageTriggers": [
            "Create ... with LoomStrokeMCP",
            "Generate ... with LoomStroke MCP",
            "create/make/generate with LoomStroke MCP",
        ],
        "rules": [
            (
                "For creation requests other than comics, call loomstroke_create first "
                "with the original user request in the request field. For comics, follow "
                "comicWorkflow to prepare a script and use loomstroke_create_comic_page."
            ),
            (
                "For manga/comic/4-panel/comic-page requests, the generic router uses request "
                "as literal script; it does not invent a story or forward a separate "
                "script/story argument. Comic tools produce a preview, not artwork generation."
            ),
            (
                "Treat blocked=true as a completed safe partial result, not a tool-call "
                "failure. Report created preview assets and the blocked write/backend step."
            ),
            (
                "Only follow nextActions after user-visible confirmation when they involve "
                "file writes, live canvas mutation, external services, or production submit."
            ),
        ],
        "comicWorkflow": comic_agent_guide(),
        "safeFallbacks": {
            "confirmationlessStdio": (
                "Use returned assetId/previewFallback.assetId. File package export may be "
                "blocked without failing the tool call."
            ),
            "unsupportedIntent": (
                "Use the returned GraphicPlan or ProductionRecipe dry-run plan and report "
                "the suggested nextActions."
            ),
        },
        "doNotExpose": [
            "credential values",
            "session tokens",
            "private endpoints",
            "raw binary payloads",
        ],
    }
