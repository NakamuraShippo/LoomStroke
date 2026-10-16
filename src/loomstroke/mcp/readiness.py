"""Credential-free MCP runtime readiness diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loomstroke.platform.settings_schema import MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW

from .editing_readiness import build_editing_readiness, known_document_closed
from .generation_capabilities import generation_submitter_capabilities
from .safety import scrub_secrets
from .sam3_segment_policies import segment_policy_capabilities


def build_readiness_report(
    service: Any,
    *,
    wgpu_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return machine-readable readiness without reading credentials."""

    from .panel_tools import panel_edit_available  # noqa: PLC0415 - avoid readiness import cycle

    wgpu = scrub_secrets(dict(wgpu_status or {}))
    has_live_canvas = getattr(
        service, "canvas_service", None
    ) is not None and not known_document_closed(service)
    has_scene_source = bool(service._has_scene_source())  # noqa: SLF001
    roots = [str(root) for root in getattr(service.safety, "roots", ())]
    external_allowed = bool(getattr(service.safety, "external_services_allowed", False))
    confirmation_policy = str(getattr(service.safety, "confirmation_policy", "always_ask"))
    confirmation_callback = getattr(service.safety, "confirmation_callback", None)
    callback_available = confirmation_callback is not None
    hard_confirmations_skipped = bool(getattr(service.safety, "hard_confirmations_skipped", False))
    hard_confirmation_available = callback_available or hard_confirmations_skipped
    mode = str(getattr(service, "mode", "stdio"))
    generation_capabilities = generation_submitter_capabilities(service)
    generation_submitter = generation_capabilities["available"]
    preset_applier = getattr(service, "_ai_effect_preset_applier", None) is not None
    preset_lister = getattr(service, "_ai_effect_preset_lister", None) is not None
    wgpu_available = bool(wgpu.get("available"))
    panel_canvas_ready = panel_edit_available(service)
    canvas_agent_runtime = getattr(service, "_canvas_agent", None)
    planner_readiness = (
        canvas_agent_runtime.planner_readiness()
        if canvas_agent_runtime is not None
        and callable(getattr(canvas_agent_runtime, "planner_readiness", None))
        else {
            "externalPlannerAvailable": False,
            "externalPlannerEnabled": False,
            "externalPlannerBlockedReason": "gateway_unavailable",
            "readOnlyToolAllowlistHash": "",
            "lastPlannerProvider": "deterministic",
            "plannerCredentialReadPerformed": False,
        }
    )

    warnings: list[dict[str, Any]] = []
    if has_live_canvas:
        warnings.append(
            _warning(
                "live_document_target",
                "medium",
                "Side-effecting live MCP tools operate on the currently open LoomStroke document.",
                "Use preview, changeset, and confirmation flows before applying changes.",
            )
        )
    if confirmation_policy == MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW:
        warnings.append(
            _warning(
                "confirmation_always_allow",
                "high",
                "Agent Approval is set to always_allow.",
                "Use always_ask for public or shared MCP client setups.",
            )
        )
    if hard_confirmations_skipped:
        warnings.append(
            _warning(
                "hard_confirmations_skipped",
                "high",
                "Additional MCP confirmation dialogs are disabled under always_allow.",
                "Turn off Skip additional confirmation dialogs to confirm protected writes.",
            )
        )
    if mode == "stdio" and not has_live_canvas:
        warnings.append(
            _warning(
                "headless_stdio_mode",
                "info",
                "stdio mode can inspect loaded files but is not connected to the live app canvas.",
                "Use the live HTTP endpoint from LoomStroke Preferences for live document work.",
            )
        )

    tools = [
        _tool(
            "loomstroke_get_capabilities",
            ready=True,
            state="ready",
            read_only=True,
        ),
        _tool_with_blocks(
            "loomstroke_get_edit_context",
            _scene_blocks(has_scene_source),
            read_only=True,
        ),
        _tool_with_blocks(
            "loomstroke_plan_canvas_edit",
            _scene_blocks(has_scene_source),
            read_only=True,
            notes=[
                _warning(
                    "phase0_local_planner",
                    "info",
                    "Canvas Agent planning is local and read-only in Phase 0.",
                    "Use preview/apply tools only after later CanvasChangeset phases land.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_create_canvas_changeset",
            _scene_blocks(has_scene_source),
            notes=[
                _warning(
                    "canvas_changeset_draft_only",
                    "info",
                    "Canvas Agent changeset creation drafts structured operations only.",
                    "Preview and approval are required before any future apply path.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_preview_canvas_changeset",
            _scene_blocks(has_scene_source),
            notes=[
                _warning(
                    "preview_no_document_mutation",
                    "info",
                    "Canvas Agent preview creates a structured preview artifact only.",
                    "It does not mutate the document, history, layer tree, or asset files.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_answer_canvas_clarification",
            _scene_blocks(has_scene_source),
            notes=[
                _warning(
                    "clarification_resolution_no_mutation",
                    "info",
                    "Clarification answers patch the run plan only.",
                    "Preview approval is still required before any canvas apply.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_apply_canvas_changeset",
            _scene_blocks(has_scene_source)
            + _live_canvas_blocks(has_live_canvas)
            + _hard_confirmation_blocks(hard_confirmation_available),
            current_document_affects=True,
            notes=[
                _warning(
                    "command_backed_canvas_apply",
                    "info",
                    (
                        "Canvas Agent apply requires a preview approval ticket and one "
                        "undoable Command."
                    ),
                    "Review the preview before applying to the live document.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_verify_canvas_edit",
            _scene_blocks(has_scene_source),
            read_only=True,
            notes=[
                _warning(
                    "verification_metadata_only",
                    "info",
                    "Canvas Agent verification compares secret-free context metadata only.",
                    "Use manual visual review for final artwork approval.",
                )
            ],
        ),
        _tool(
            "loomstroke_validate_comic_plan",
            ready=True,
            state="ready",
            read_only=True,
        ),
        _tool(
            "loomstroke_plan_comic_page",
            ready=True,
            state="ready",
            read_only=True,
        ),
        _tool(
            "loomstroke_create",
            ready=True,
            state="ready",
            file_write=True,
            notes=[
                _warning(
                    "preferred_generic_creation_entry",
                    "info",
                    (
                        "Use this one-shot router first for natural-language "
                        "LoomStroke MCP creation requests."
                    ),
                    (
                        "It routes to high-level creators and returns structured "
                        "blocked results for unavailable writes or backends."
                    ),
                )
            ],
        ),
        _tool(
            "loomstroke_create_comic_page",
            ready=True,
            state="ready",
            file_write=True,
            notes=[
                _warning(
                    "safe_partial_completion",
                    "info",
                    (
                        "The one-shot comic workflow returns a preview asset even when "
                        "file export is unavailable."
                    ),
                    (
                        "Use this tool for natural-language comic creation requests from "
                        "generic MCP clients."
                    ),
                )
            ],
        ),
        _tool(
            "loomstroke_render_comic_preview",
            ready=True,
            state=(
                "partial"
                if _confirmation_blocks(service, "loomstroke_render_comic_preview")
                else "ready"
            ),
            file_write=True,
            blocked_reasons=_confirmation_blocks(
                service,
                "loomstroke_render_comic_preview",
            ),
            notes=[
                _warning(
                    "output_path_optional",
                    "info",
                    (
                        "Comic preview can render/register an asset without roots; "
                        "outputPath writes still require an allowed MCP root."
                    ),
                    "Use loomstroke_export_comic_package for external repo file packages.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_export_comic_package",
            _root_blocks(roots) + _confirmation_blocks(service, "loomstroke_export_comic_package"),
            file_write=True,
        ),
        _tool_with_blocks(
            "loomstroke_create_comic_changeset",
            _hard_confirmation_blocks(hard_confirmation_available),
            current_document_affects=False,
        ),
        _tool_with_blocks(
            "loomstroke_apply_changeset:comic",
            _hard_confirmation_blocks(hard_confirmation_available)
            + _live_canvas_blocks(has_live_canvas),
            current_document_affects=True,
            notes=[
                _warning(
                    "command_backed_editable_apply",
                    "info",
                    "Comic editable apply uses a changeset and one Command-backed undo entry.",
                    "Review the changeset before applying it to the live document.",
                )
            ],
        ),
        _tool(
            "loomstroke_get_ai_effect_catalog",
            ready=True,
            state="ready",
            read_only=True,
            notes=(
                []
                if preset_lister
                else [
                    _blocked(
                        "preset_library_not_attached",
                        "No live AI Effect preset library is attached; catalog may be empty.",
                    )
                ]
            ),
        ),
        _tool_with_blocks(
            "loomstroke_render_preview",
            _scene_blocks(has_scene_source) + _wgpu_blocks(wgpu_available, wgpu),
            read_only=True,
        ),
        _tool_with_blocks(
            "loomstroke_batch_render",
            _scene_blocks(has_scene_source)
            + _root_blocks(roots)
            + _wgpu_blocks(wgpu_available, wgpu)
            + _confirmation_blocks(service, "loomstroke_batch_render"),
            file_write=True,
        ),
        _tool_with_blocks(
            "loomstroke_create_asset_overlay_preview",
            _root_blocks(roots)
            + _confirmation_blocks(service, "loomstroke_create_asset_overlay_preview"),
            file_write=True,
            notes=[
                _warning(
                    "asset_preview_only",
                    "info",
                    "Overlay preview writes a new PNG asset and does not mutate the canvas.",
                    "Use changeset/command-backed flows for live document mutation.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_create_speech_bubble_overlay",
            _root_blocks(roots)
            + _confirmation_blocks(service, "loomstroke_create_speech_bubble_overlay"),
            file_write=True,
            notes=[
                _warning(
                    "asset_preview_only",
                    "info",
                    "Speech bubble overlay writes a new PNG asset and does not mutate the canvas.",
                    "Use changeset/command-backed flows for live document mutation.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_import_asset",
            _root_blocks(roots),
            read_only=True,
            notes=[
                _warning(
                    "rooted_file_read",
                    "info",
                    "Asset import reads PNG/JPEG/WebP files only from configured MCP roots.",
                    "Add the source asset directory to MCP roots before import.",
                )
            ],
        ),
        _tool_with_blocks(
            "loomstroke_export_asset",
            _root_blocks(roots) + _confirmation_blocks(service, "loomstroke_export_asset"),
            file_write=True,
        ),
        _tool(
            "loomstroke_wait_job",
            ready=True,
            state="ready",
            read_only=True,
        ),
        _tool(
            "loomstroke_wait_layer_ready",
            ready=True,
            state="ready",
            read_only=True,
        ),
        _tool_with_blocks(
            "loomstroke_apply_ai_effect_preset",
            _applier_blocks(preset_applier)
            + (_live_canvas_blocks(False) if known_document_closed(service) else [])
            + _confirmation_blocks(service, "loomstroke_apply_ai_effect_preset"),
            current_document_affects=True,
        ),
        _tool_with_blocks(
            "loomstroke_submit_generation",
            _external_blocks(external_allowed)
            + _submitter_blocks(generation_submitter)
            + _confirmation_blocks(service, "loomstroke_submit_generation"),
            external_service=True,
            current_document_affects=True,
        ),
        _tool_with_blocks(
            "loomstroke_get_panels",
            _live_canvas_blocks(panel_canvas_ready),
            read_only=True,
        ),
        _tool_with_blocks(
            "loomstroke_preview_panel_edit",
            _live_canvas_blocks(panel_canvas_ready),
            read_only=True,
        ),
        _tool_with_blocks(
            "loomstroke_apply_panel_edit",
            _live_canvas_blocks(panel_canvas_ready)
            + _hard_confirmation_blocks(hard_confirmation_available),
            current_document_affects=True,
        ),
        _tool_with_blocks(
            "loomstroke_pct_write_tools",
            _hard_confirmation_blocks(hard_confirmation_available),
            current_document_affects=False,
            notes=(
                []
                if hard_confirmations_skipped
                else [
                    _warning(
                        "pct_hard_confirmation",
                        "info",
                        (
                            "PCT write tools require confirmation even when Agent Approval "
                            "is always_allow."
                        ),
                        (
                            "Review changeset, Bible, export, recipe, and decision payloads "
                            "before approval."
                        ),
                    )
                ]
            ),
        ),
    ]

    editing = build_editing_readiness(service)
    tools.extend(editing["tools"])
    tool_map = {item["tool"]: item for item in tools}
    return {
        "schemaVersion": "mcp.readiness.v1",
        "aiSelectionSegmentPolicies": segment_policy_capabilities(),
        "mode": mode,
        "editing": editing,
        "connection": {
            "kind": _connection_kind(mode, has_live_canvas),
            "hasLiveCanvas": has_live_canvas,
            "hasSceneSource": has_scene_source,
            "hasGenerationSubmitter": generation_submitter,
            "productionRecipeGenerationSupported": generation_capabilities["productionCandidates"],
            "hasAIEffectPresetLibrary": preset_lister,
            "hasAIEffectPresetApplier": preset_applier,
        },
        "clientRequirements": {
            "localHttpBindHost": "127.0.0.1",
            "originHeaderRequired": False,
            "originPolicy": (
                "Explicit Origin headers must match allowedOrigins. Missing Origin is "
                "accepted only for loopback requests with a valid session token."
            ),
            "missingOriginAcceptedForLoopbackAuth": True,
            "allowedOrigins": list(getattr(service.safety, "allowed_origins", ())),
            "sessionTokenRequired": True,
            "sessionTokenResponseHeader": False,
            "urlQueryTokenAccepted": False,
            "tokenAcceptedIn": [
                "Authorization: Bearer <token>",
                "Mcp-Session-Token request header",
            ],
        },
        "guards": {
            "externalServicesAllowed": external_allowed,
            "confirmationPolicy": confirmation_policy,
            "confirmationCallbackAvailable": callback_available,
            "hardConfirmationRequired": not hard_confirmations_skipped,
            "hardConfirmationsSkipped": hard_confirmations_skipped,
            "roots": roots,
            "credentialReadPerformed": False,
            "credentialValuesIncluded": False,
            "directCanvasMutationAllowed": False,
        },
        "summary": {
            "generationReady": tool_map["loomstroke_submit_generation"]["ready"],
            "renderPreviewReady": tool_map["loomstroke_render_preview"]["ready"],
            "assetOverlayPreviewReady": tool_map["loomstroke_create_asset_overlay_preview"][
                "ready"
            ],
            "speechBubbleOverlayReady": tool_map["loomstroke_create_speech_bubble_overlay"][
                "ready"
            ],
            "assetImportReady": tool_map["loomstroke_import_asset"]["ready"],
            "genericCreateReady": tool_map["loomstroke_create"]["ready"],
            "comicPreviewReady": tool_map["loomstroke_render_comic_preview"]["ready"],
            "comicPlanningReady": tool_map["loomstroke_plan_comic_page"]["ready"],
            "comicCreateReady": tool_map["loomstroke_create_comic_page"]["ready"],
            "comicPackageReady": tool_map["loomstroke_export_comic_package"]["ready"],
            "comicEditableApplyReady": tool_map["loomstroke_apply_changeset:comic"]["ready"],
            "exportReady": tool_map["loomstroke_export_asset"]["ready"],
            "liveMutationReady": tool_map["loomstroke_apply_ai_effect_preset"]["ready"],
            "pctWritesReady": tool_map["loomstroke_pct_write_tools"]["ready"],
            "canvasAgentReadOnlyReady": tool_map["loomstroke_get_edit_context"]["ready"],
            "canvasAgentPlanningReady": tool_map["loomstroke_plan_canvas_edit"]["ready"],
            "canvasAgentChangesetReady": tool_map["loomstroke_create_canvas_changeset"]["ready"],
            "canvasAgentPreviewReady": tool_map["loomstroke_preview_canvas_changeset"]["ready"],
            "canvasAgentClarificationReady": tool_map["loomstroke_answer_canvas_clarification"][
                "ready"
            ],
            "canvasAgentApplyReady": tool_map["loomstroke_apply_canvas_changeset"]["ready"],
            "canvasAgentVerifyReady": tool_map["loomstroke_verify_canvas_edit"]["ready"],
        },
        "canvasAgent": {
            "schemaVersion": "mcp.canvas_agent_readiness.v1",
            "available": has_scene_source,
            "readOnlyLoop": tool_map["loomstroke_plan_canvas_edit"]["ready"],
            "canCreateChangeset": tool_map["loomstroke_create_canvas_changeset"]["ready"],
            "canPreviewChangeset": tool_map["loomstroke_preview_canvas_changeset"]["ready"],
            "canResolveClarification": tool_map["loomstroke_answer_canvas_clarification"]["ready"],
            "canApplyChangeset": tool_map["loomstroke_apply_canvas_changeset"]["ready"],
            "canVerifyCanvasEdit": tool_map["loomstroke_verify_canvas_edit"]["ready"],
            "externalPlannerAvailable": planner_readiness["externalPlannerAvailable"],
            "externalPlannerEnabled": planner_readiness["externalPlannerEnabled"],
            "externalPlannerBlockedReason": planner_readiness["externalPlannerBlockedReason"],
            "readOnlyToolAllowlistHash": planner_readiness["readOnlyToolAllowlistHash"],
            "lastPlannerProvider": planner_readiness["lastPlannerProvider"],
            "plannerCredentialReadPerformed": planner_readiness["plannerCredentialReadPerformed"],
            "credentialReadPerformed": False,
            "blockedReasons": _scene_blocks(has_scene_source),
            "applyBlockedReasons": tool_map["loomstroke_apply_canvas_changeset"]["blockedReasons"],
            "recommendedActions": (
                []
                if has_scene_source
                else [
                    "Connect to the live app endpoint or start stdio with --document.",
                ]
            ),
        },
        "comicWorkflow": {
            "schemaVersion": "mcp.comic_workflow_readiness.v1",
            "planSchemaVersion": "mcp.comic_page_plan.v1",
            "genericCreateTool": "loomstroke_create",
            "planningReady": tool_map["loomstroke_plan_comic_page"]["ready"],
            "createReady": tool_map["loomstroke_create_comic_page"]["ready"],
            "previewOnlyReady": tool_map["loomstroke_render_comic_preview"]["ready"],
            "externalPackageReady": tool_map["loomstroke_export_comic_package"]["ready"],
            "editableDraftReady": tool_map["loomstroke_create_comic_changeset"]["ready"],
            "editableApplyReady": tool_map["loomstroke_apply_changeset:comic"]["ready"],
            "textFitEngine": "deterministic_headless_safe",
            "liveCanvasRequiredForApply": True,
            "rootsRequiredForPackage": True,
            "responseMetadataOmitsComicText": True,
            "previewAndPackageMetadataOmitsComicText": True,
            "workflowResponseIncludesPlanText": True,
            "credentialReadPerformed": False,
        },
        "graphicCompletion": {
            "schemaVersion": "mcp.graphic_completion_readiness.v1",
            "assetImportReady": tool_map["loomstroke_import_asset"]["ready"],
            "assetOverlayPreviewReady": tool_map["loomstroke_create_asset_overlay_preview"][
                "ready"
            ],
            "speechBubbleOverlayReady": tool_map["loomstroke_create_speech_bubble_overlay"][
                "ready"
            ],
            "comicPreviewReady": tool_map["loomstroke_render_comic_preview"]["ready"],
            "generationReady": tool_map["loomstroke_submit_generation"]["ready"],
            "mcpOnlyRasterFinalizeReady": (
                tool_map["loomstroke_import_asset"]["ready"]
                and tool_map["loomstroke_create_asset_overlay_preview"]["ready"]
                and tool_map["loomstroke_create_speech_bubble_overlay"]["ready"]
            ),
            "externalImageGeneratorRequired": False,
            "localPillowRequired": False,
            "credentialReadPerformed": False,
        },
        "warnings": warnings,
        "tools": tools,
        "renderPreview": {
            "backend": "wgpu-offscreen",
            "available": wgpu_available,
            "status": wgpu,
        },
        "responseSchemas": {
            "pctEnvelope": "pct.tool.v1",
            "legacyToolResponses": "direct structuredContent payloads remain supported",
            "newFieldsPolicy": "additive",
        },
        "resources": {
            "self": "loomstroke://diagnostics/readiness",
            "capabilities": "loomstroke://capabilities",
            "agentGuide": "loomstroke://agent-guide",
            "productionCapabilities": "loomstroke://production/capabilities",
        },
    }


def _connection_kind(mode: str, has_live_canvas: bool) -> str:
    if has_live_canvas:
        return "live_app"
    if mode == "live_http":
        return "headless_http"
    return "headless_stdio"


def _tool_with_blocks(
    name: str,
    blocks: list[dict[str, Any]],
    *,
    read_only: bool = False,
    file_write: bool = False,
    external_service: bool = False,
    current_document_affects: bool = False,
    notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _tool(
        name,
        ready=not blocks,
        state="ready" if not blocks else "blocked",
        read_only=read_only,
        file_write=file_write,
        external_service=external_service,
        current_document_affects=current_document_affects,
        blocked_reasons=blocks,
        notes=notes or [],
    )


def _tool(
    name: str,
    *,
    ready: bool,
    state: str,
    read_only: bool = False,
    file_write: bool = False,
    external_service: bool = False,
    current_document_affects: bool = False,
    blocked_reasons: list[dict[str, Any]] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "tool": name,
        "ready": ready,
        "state": state,
        "readOnly": read_only,
        "fileWrite": file_write,
        "externalService": external_service,
        "currentDocumentAffects": current_document_affects,
        "blockedReasons": blocked_reasons or [],
        "notes": notes or [],
    }


def _blocked(code: str, message: str, action: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if action:
        payload["action"] = action
    return payload


def _warning(code: str, severity: str, message: str, action: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "action": action,
    }


def _scene_blocks(has_scene_source: bool) -> list[dict[str, Any]]:
    if has_scene_source:
        return []
    return [
        _blocked(
            "scene_source_unavailable",
            "No live canvas, snapshot provider, or loaded headless document is available.",
            "Connect to the live app endpoint or start stdio with --document.",
        )
    ]


def _root_blocks(roots: list[str]) -> list[dict[str, Any]]:
    if roots:
        return []
    return [
        _blocked(
            "roots_not_configured",
            "File I/O MCP tools require at least one allowed root.",
            "Configure MCP roots in Preferences or pass --root to the MCP server.",
        )
    ]


def _live_canvas_blocks(has_live_canvas: bool) -> list[dict[str, Any]]:
    if has_live_canvas:
        return []
    return [
        _blocked(
            "live_canvas_unavailable",
            "Command-backed editable apply requires the live LoomStroke canvas.",
            "Use preview/package tools in headless mode, or connect to the live app endpoint.",
        )
    ]


def _wgpu_blocks(available: bool, status: Mapping[str, Any]) -> list[dict[str, Any]]:
    if available:
        return []
    reason = str(status.get("reason") or "unavailable")
    return [
        _blocked(
            "wgpu_preview_unavailable",
            f"wgpu preview backend is unavailable: {reason}",
            "Use scene inspection resources or fix the wgpu runtime before preview/batch render.",
        )
    ]


def _external_blocks(external_allowed: bool) -> list[dict[str, Any]]:
    if external_allowed:
        return []
    return [
        _blocked(
            "privacy_gate_disabled",
            "External AI services are disabled by privacy/allow_external_services.",
            "Enable Allow external AI services before submitting generation jobs.",
        )
    ]


def _submitter_blocks(has_submitter: bool) -> list[dict[str, Any]]:
    if has_submitter:
        return []
    return [
        _blocked(
            "generation_submitter_unavailable",
            "No live AI Effect generation submitter is attached.",
            "Use the live app MCP endpoint with AI Effect runtime initialized.",
        )
    ]


def _applier_blocks(has_applier: bool) -> list[dict[str, Any]]:
    if has_applier:
        return []
    return [
        _blocked(
            "ai_effect_applier_unavailable",
            "No live AI Effect preset applier is attached.",
            "Use the live app MCP endpoint with AI Effect runtime initialized.",
        )
    ]


def _confirmation_blocks(service: Any, operation: str) -> list[dict[str, Any]]:
    if not service.safety.requires_confirmation(operation):
        return []
    if service.safety.confirmation_callback is not None:
        return []
    return [
        _blocked(
            "confirmation_callback_unavailable",
            (
                "This MCP operation requires user confirmation, but no confirmation callback "
                "is attached."
            ),
            "Use the live app endpoint or adjust Agent Approval intentionally.",
        )
    ]


def _hard_confirmation_blocks(callback_available: bool) -> list[dict[str, Any]]:
    if callback_available:
        return []
    return [
        _blocked(
            "hard_confirmation_callback_unavailable",
            "Confirmed write tools require a live confirmation callback.",
            "Use the live app endpoint before applying live or persistent writes.",
        )
    ]
