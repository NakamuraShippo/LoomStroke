"""MCP tools/list and tools/call handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from . import editing_tools
from .comic_style import COMIC_ART_GUIDANCE, COMIC_STYLE_SCHEMA
from .panel_tools import TOOL_BY_NAME as PANEL_TOOL_BY_NAME
from .panel_tools import TOOLS as PANEL_TOOLS
from .panel_tools import dispatch as dispatch_panel_tool
from .serialization import json_text_content
from .types import McpStructuredError, McpToolSpec, ReasonCode

_EMPTY_OBJECT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
_GENERIC_OBJECT_SCHEMA = {"type": "object", "additionalProperties": True}


TOOLS: tuple[McpToolSpec, ...] = (
    McpToolSpec(
        "loomstroke_get_capabilities",
        "Return LoomStroke MCP capability, guard, wgpu, and privacy status.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_get_readiness",
        "Return the SAM3 read-only readiness resource payload.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_segment_asset",
        "Run SAM3 segmentation for a stored source snapshot and create candidate/job/mask assets.",
        {
            "type": "object",
            "required": ["sourceSnapshotId", "prompt"],
            "properties": {
                "sourceSnapshotId": {"type": "string"},
                "requestId": {"type": "string"},
                "prompt": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "mode": {
                                    "type": "string",
                                    "enum": ["text", "point", "box", "mask", "exemplar", "mixed"],
                                },
                                "text": {"type": "string"},
                                "points": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "x": {"type": "number"},
                                            "y": {"type": "number"},
                                            "label": {
                                                "type": "string",
                                                "enum": ["positive", "negative"],
                                            },
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "boxes": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "x": {"type": "number"},
                                            "y": {"type": "number"},
                                            "width": {"type": "number"},
                                            "height": {"type": "number"},
                                            "label": {"type": "string"},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "maskAssetId": {"type": "string"},
                                "exemplarAssetIds": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "negativeText": {"type": "string"},
                                "locale": {"type": "string"},
                                "normalizedText": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    ],
                },
                "maxCandidates": {"type": "integer", "minimum": 1, "maximum": 64},
                "minScore": {"type": "number", "minimum": 0, "maximum": 1},
                "maskResolutionPolicy": {"type": "string"},
                "postprocessPolicy": {"type": "object", "additionalProperties": True},
                "metadata": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_segment_current",
        "Render the current scene to a SAM3 source snapshot and run segmentation.",
        {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "requestId": {"type": "string"},
                "pageId": {"type": "string"},
                "prompt": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "mode": {
                                    "type": "string",
                                    "enum": ["text", "point", "box", "mask", "exemplar", "mixed"],
                                },
                                "text": {"type": "string"},
                                "points": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "x": {"type": "number"},
                                            "y": {"type": "number"},
                                            "label": {
                                                "type": "string",
                                                "enum": ["positive", "negative"],
                                            },
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "boxes": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "x": {"type": "number"},
                                            "y": {"type": "number"},
                                            "width": {"type": "number"},
                                            "height": {"type": "number"},
                                            "label": {"type": "string"},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "maskAssetId": {"type": "string"},
                                "exemplarAssetIds": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "negativeText": {"type": "string"},
                                "locale": {"type": "string"},
                                "normalizedText": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    ],
                },
                "maxCandidates": {"type": "integer", "minimum": 1, "maximum": 64},
                "minScore": {"type": "number", "minimum": 0, "maximum": 1},
                "maskResolutionPolicy": {"type": "string"},
                "postprocessPolicy": {"type": "object", "additionalProperties": True},
                "metadata": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_cancel_job",
        "Cancel a SAM3 provider job and update its SAM3 job status resource when accepted.",
        {
            "type": "object",
            "required": ["jobId"],
            "properties": {
                "jobId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_create_overlay_preview",
        (
            "Create a SAM3 overlay preview asset from an existing candidate set "
            "without canvas mutation."
        ),
        {
            "type": "object",
            "required": ["candidateSetId"],
            "properties": {
                "candidateSetId": {"type": "string"},
                "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                "colorMode": {
                    "type": "string",
                    "enum": ["instance", "single", "heat"],
                },
                "includeRejected": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_apply_candidate_as_selection",
        "Apply a SAM3 candidate mask to the live canvas selection through Undo/Redo.",
        {
            "type": "object",
            "required": ["candidateSetId", "candidateId"],
            "properties": {
                "candidateSetId": {"type": "string"},
                "candidateId": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": ["replace", "add", "subtract", "intersect"],
                    "default": "replace",
                },
                "pageId": {"type": "string"},
                "requestId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_apply_candidate_as_layer_mask",
        "Apply a SAM3 candidate mask to a live canvas layer mask through Undo/Redo.",
        {
            "type": "object",
            "required": ["candidateSetId", "candidateId", "targetLayerId"],
            "properties": {
                "candidateSetId": {"type": "string"},
                "candidateId": {"type": "string"},
                "targetLayerId": {"type": "string"},
                "pageId": {"type": "string"},
                "requestId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_export_mask_package",
        "Export a SAM3 candidate set as a rooted comic mask package without live canvas mutation.",
        {
            "type": "object",
            "required": ["candidateSetId", "outputDirectory"],
            "properties": {
                "candidateSetId": {"type": "string"},
                "outputDirectory": {"type": "string"},
                "category": {"type": "string", "default": "character"},
                "candidateIds": {"type": "array", "items": {"type": "string"}},
                "includePreviews": {"type": "boolean", "default": False},
                "panelBboxes": {"type": "object", "additionalProperties": True},
                "categoryByCandidateId": {"type": "object", "additionalProperties": True},
                "acceptedCandidateIds": {"type": "array", "items": {"type": "string"}},
                "packageId": {"type": "string"},
                "sourceDocumentId": {"type": "string"},
                "sourcePageId": {"type": "string"},
                "metadataPath": {"type": "string"},
                "requestId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_get_source_snapshot",
        "Return a SAM3 source snapshot read-only resource payload.",
        {
            "type": "object",
            "required": ["sourceSnapshotId"],
            "properties": {
                "sourceSnapshotId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_get_candidate_set",
        "Return a SAM3 candidate set read-only resource payload.",
        {
            "type": "object",
            "required": ["candidateSetId"],
            "properties": {
                "candidateSetId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_get_candidate",
        "Return a SAM3 candidate read-only resource payload.",
        {
            "type": "object",
            "required": ["candidateId"],
            "properties": {
                "candidateId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_get_mask_asset",
        "Return a SAM3 mask asset read-only resource payload.",
        {
            "type": "object",
            "required": ["maskAssetId"],
            "properties": {
                "maskAssetId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_get_preview_asset",
        "Return a SAM3 preview asset read-only resource payload.",
        {
            "type": "object",
            "required": ["previewAssetId"],
            "properties": {
                "previewAssetId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_sam3_get_job_status",
        "Return a SAM3 job status read-only resource payload.",
        {
            "type": "object",
            "required": ["jobId"],
            "properties": {
                "jobId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_list_documents",
        "List live or headless documents visible to the MCP service.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_scene_snapshot",
        "Return the current RenderSceneSnapshot as JSON-safe data.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_graphic_plan",
        "Create a side-effect-free local GraphicPlan from a user prompt.",
        {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string", "maxLength": 8000},
                "target": {
                    "type": "object",
                    "properties": {
                        "documentId": {"type": "string"},
                        "pageId": {"type": "string"},
                        "layerId": {"type": "string"},
                        "assetId": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "dryRun": {"type": "boolean", "default": True},
                "clientRequestId": {"type": "string", "maxLength": 128},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_edit_context",
        "Return a secret-free read-only Canvas Agent edit context.",
        {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "pageId": {"type": "string"},
                "includeThumbnail": {"type": "boolean", "default": False},
                "includePanelFrames": {"type": "boolean", "default": False},
                "includeHidden": {"type": "boolean", "default": True},
                "maxLayerCount": {"type": "integer", "minimum": 1, "maximum": 256},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_plan_canvas_edit",
        "Create a read-only CanvasEditPlan from natural language and edit context.",
        {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string", "maxLength": 8000},
                "request": {"type": "string", "maxLength": 8000},
                "editContext": {"type": "object", "additionalProperties": True},
                "context": {"type": "object", "additionalProperties": True},
                "clientRequestId": {"type": "string", "maxLength": 128},
                "plannerProvider": {
                    "type": "string",
                    "enum": ["deterministic", "llm_read_only"],
                    "default": "deterministic",
                },
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_create_canvas_changeset",
        "Create a non-mutating CanvasChangeset draft from a Canvas Agent edit plan.",
        {
            "type": "object",
            "properties": {
                "plan": {"type": "object", "additionalProperties": True},
                "planId": {"type": "string"},
                "runId": {"type": "string"},
                "editContext": {"type": "object", "additionalProperties": True},
                "context": {"type": "object", "additionalProperties": True},
                "clientRequestId": {"type": "string", "maxLength": 128},
            },
            "anyOf": [
                {"required": ["plan"]},
                {"required": ["planId"]},
                {"required": ["runId"]},
            ],
            "additionalProperties": False,
        },
    ),
    McpToolSpec(
        "loomstroke_preview_canvas_changeset",
        "Validate a CanvasChangeset and return a structured preview without document mutation.",
        {
            "type": "object",
            "properties": {
                "changeset": {"type": "object", "additionalProperties": True},
                "changesetId": {"type": "string"},
                "runId": {"type": "string"},
                "editContext": {"type": "object", "additionalProperties": True},
                "context": {"type": "object", "additionalProperties": True},
                "mode": {
                    "type": "string",
                    "enum": [
                        "plan_only",
                        "scope_highlight",
                        "overlay",
                        "ghost_layer",
                        "before_after",
                        "candidate_stack",
                        "text_vector_preview",
                    ],
                },
            },
            "anyOf": [
                {"required": ["changeset"]},
                {"required": ["changesetId"]},
                {"required": ["runId"]},
            ],
            "additionalProperties": False,
        },
    ),
    McpToolSpec(
        "loomstroke_answer_canvas_clarification",
        "Resolve a Canvas Agent clarification option without preview/apply or document mutation.",
        {
            "type": "object",
            "required": ["runId", "clarificationId", "selectedOptionId"],
            "properties": {
                "runId": {"type": "string"},
                "clarificationId": {"type": "string"},
                "selectedOptionId": {"type": "string"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "targetLayerId": {"type": "string"},
                        "protectedLayerIds": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "placement": {
                            "type": "string",
                            "enum": [
                                "canvas_center",
                                "canvas_top",
                                "selection_bounds",
                                "layer_bounds",
                            ],
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    ),
    McpToolSpec(
        "loomstroke_apply_canvas_changeset",
        "Apply a preview-approved CanvasChangeset through one Command-backed undo entry.",
        {
            "type": "object",
            "properties": {
                "changeset": {"type": "object", "additionalProperties": True},
                "changesetId": {"type": "string"},
                "runId": {"type": "string"},
                "approvalTicket": {"type": "object", "additionalProperties": True},
                "approvalTicketId": {"type": "string"},
                "ticketId": {"type": "string"},
                "editContext": {"type": "object", "additionalProperties": True},
                "context": {"type": "object", "additionalProperties": True},
            },
            "anyOf": [
                {"required": ["changeset"]},
                {"required": ["changesetId"]},
                {"required": ["runId"]},
            ],
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_verify_canvas_edit",
        "Verify a Canvas Agent apply result against the current secret-free edit context.",
        {
            "type": "object",
            "properties": {
                "applyResult": {"type": "object", "additionalProperties": True},
                "applyId": {"type": "string"},
                "changesetId": {"type": "string"},
                "runId": {"type": "string"},
                "editContext": {"type": "object", "additionalProperties": True},
                "context": {"type": "object", "additionalProperties": True},
            },
            "anyOf": [
                {"required": ["applyResult"]},
                {"required": ["applyId"]},
                {"required": ["changesetId"]},
                {"required": ["runId"]},
            ],
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_create",
        "Universal one-shot creation router. Use this first when the user says "
        '"LoomStroke MCPで〜を作って", "create/make/generate with LoomStroke MCP", '
        "or gives a natural-language creation request without naming a specific tool. "
        "It routes to the safest high-level creator, returns normal structured blocked "
        "results instead of JSON-RPC failure for unavailable writes/backends, and includes "
        "nextActions when extra confirmation is required.",
        {
            "type": "object",
            "properties": {
                "request": {"type": "string", "maxLength": 8000},
                "prompt": {"type": "string", "maxLength": 8000},
                "goal": {"type": "string", "maxLength": 8000},
                "script": {"type": "string", "maxLength": 8000},
                "story": {"type": "string", "maxLength": 8000},
                "instructions": {"type": "string", "maxLength": 8000},
                "domain": {
                    "type": "string",
                    "description": "Optional hint such as comic_page, character_design, "
                    "character_sheet, game_asset_3d, or graphic_plan.",
                },
                "page": {"type": "object", "additionalProperties": True},
                "style": {"type": "object", "additionalProperties": True},
                "assetIds": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "object", "additionalProperties": True},
                "export": {"type": "object", "additionalProperties": True},
                "input": {"type": "object", "additionalProperties": True},
                "outputPath": {"type": "string"},
                "outputDirectory": {"type": "string"},
                "fileName": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
                "registerAsset": {"type": "boolean", "default": True},
                "baseAssetId": {"type": "string"},
                "exportPackage": {"type": "boolean", "default": False},
            },
            "anyOf": [
                {"required": ["request"]},
                {"required": ["prompt"]},
                {"required": ["goal"]},
                {"required": ["script"]},
                {"required": ["story"]},
                {"required": ["instructions"]},
            ],
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_validate_comic_plan",
        "Validate a ComicPagePlan without file writes or live canvas mutation.",
        {
            "type": "object",
            "required": ["plan"],
            "properties": {"plan": {"type": "object", "additionalProperties": True}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_plan_comic_page",
        "Create a deterministic ComicPagePlan from a comic script without side effects. "
        + COMIC_ART_GUIDANCE,
        {
            "type": "object",
            "required": ["script"],
            "properties": {
                "script": {"type": "string", "maxLength": 8000},
                "page": {"type": "object", "additionalProperties": True},
                "style": COMIC_STYLE_SCHEMA,
                "assetIds": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "object", "additionalProperties": True},
                "export": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_create_comic_page",
        "Comic layout workflow. Accepts a script, plans and validates a page, renders "
        "a preview asset, and downgrades unavailable file exports to structured blocked "
        "results instead of failing the tool call. " + COMIC_ART_GUIDANCE,
        {
            "type": "object",
            "required": ["script"],
            "properties": {
                "script": {"type": "string", "maxLength": 8000},
                "page": {"type": "object", "additionalProperties": True},
                "style": COMIC_STYLE_SCHEMA,
                "assetIds": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "object", "additionalProperties": True},
                "export": {"type": "object", "additionalProperties": True},
                "outputPath": {
                    "type": "string",
                    "description": "Optional rooted PNG output path. If confirmation is "
                    "unavailable, the workflow still returns an asset-only preview.",
                },
                "outputDirectory": {
                    "type": "string",
                    "description": "Optional rooted package directory. If confirmation is "
                    "unavailable, package export is returned as blocked with a preview fallback.",
                },
                "fileName": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
                "registerAsset": {"type": "boolean", "default": True},
                "baseAssetId": {"type": "string"},
                "exportPackage": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_render_comic_preview",
        "Render a ComicPagePlan to a PNG preview without mutating the live document. "
        "Omit outputPath for Claude Desktop/headless stdio asset-only previews; "
        "outputPath requests a rooted file write and requires a confirmation-capable client.",
        {
            "type": "object",
            "required": ["plan"],
            "properties": {
                "plan": {"type": "object", "additionalProperties": True},
                "outputPath": {
                    "type": "string",
                    "description": "Optional roots-scoped PNG output path. Omit for headless "
                    "Claude Desktop previews; file writes require user confirmation.",
                },
                "overwrite": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only applies when outputPath is provided.",
                },
                "registerAsset": {
                    "type": "boolean",
                    "default": True,
                    "description": "Register the rendered PNG as an in-memory MCP asset.",
                },
                "baseAssetId": {
                    "type": "string",
                    "description": "Optional registered raster asset used as the page background.",
                },
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_export_comic_package",
        "Export a ComicPagePlan package with PNG preview, plan JSON, and text fit report. "
        "This always writes files under MCP roots and requires a confirmation-capable client; "
        "Claude Desktop/headless stdio should use loomstroke_render_comic_preview without "
        "outputPath instead.",
        {
            "type": "object",
            "required": ["plan", "outputDirectory"],
            "properties": {
                "plan": {"type": "object", "additionalProperties": True},
                "outputDirectory": {
                    "type": "string",
                    "description": "Roots-scoped output directory. Requires user "
                    "confirmation; unavailable in confirmation-less headless stdio.",
                },
                "fileName": {
                    "type": "string",
                    "description": "Optional preview PNG file name inside outputDirectory.",
                },
                "overwrite": {"type": "boolean", "default": False},
                "registerAsset": {"type": "boolean", "default": True},
                "baseAssetId": {"type": "string"},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_create_comic_changeset",
        "Create a confirmed editable comic changeset draft from a ComicPagePlan.",
        {
            "type": "object",
            "required": ["plan"],
            "properties": {
                "plan": {"type": "object", "additionalProperties": True},
                "name": {"type": "string"},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_render_preview",
        "Render a PNG preview from a RenderSceneSnapshot using optional wgpu.",
        {
            "type": "object",
            "properties": {
                "pageId": {"type": "string"},
                "includeData": {"type": "boolean", "default": True},
                "registerAsset": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_list_assets",
        "List raster, generated output, and reserved asset metadata.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_import_asset",
        "Import a rooted PNG/JPEG/WebP image as a registered MCP raster asset.",
        {
            "type": "object",
            "required": ["inputPath"],
            "properties": {
                "inputPath": {"type": "string"},
                "displayName": {"type": "string", "maxLength": 256},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_list_ai_effect_presets",
        "List installed AI Effect presets available for layer placement.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_feature_catalog",
        "Return the Production Control Tower feature catalog and safety conditions.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_explain_feature",
        "Return one Production Control Tower feature explanation by stable featureId.",
        {
            "type": "object",
            "required": ["featureId"],
            "properties": {"featureId": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_ai_effect_catalog",
        "Return a credential-free AI Effect catalog across ComfyUI, NanoBanana, and GPT Image 2.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_ai_effect_preset_detail",
        "Return credential-free detail for one installed AI Effect preset.",
        {
            "type": "object",
            "required": ["presetId"],
            "properties": {"presetId": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_list_production_recipes",
        "List canonical ProductionRecipe runner recipes.",
        {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "default": "all"},
                "includeExperimental": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_production_recipe_detail",
        "Return one canonical ProductionRecipe runner detail.",
        {
            "type": "object",
            "required": ["recipeId"],
            "properties": {"recipeId": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_validate_production_recipe_input",
        "Validate and normalize ProductionRecipe input without side effects.",
        {
            "type": "object",
            "required": ["recipeId", "input"],
            "properties": {
                "recipeId": {"type": "string"},
                "input": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_plan_production_job",
        "Create a side-effect-free ProductionRecipe execution plan.",
        {
            "type": "object",
            "required": ["recipeId", "input"],
            "properties": {
                "recipeId": {"type": "string"},
                "mode": {"type": "string", "enum": ["dry_run"], "default": "dry_run"},
                "input": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_run_production_recipe",
        "Run the canonical ProductionRecipe runner in dry_run or confirmed submit mode.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_score_artifact_candidates",
        "Score ProductionRecipe candidates using deterministic QualityProfile checks.",
        _GENERIC_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_create_rework_plan",
        "Create a side-effect-free ProductionRecipe rework plan from QA failures.",
        _GENERIC_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_export_production_package",
        "Create package metadata for a completed ProductionRecipe job.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_improve_manga_page",
        "Shortcut for loomstroke_run_production_recipe with recipeId comic_page.improve.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_create_character_design",
        "Shortcut for loomstroke_run_production_recipe with recipeId character.design.create.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_create_character_sheet",
        "Shortcut for loomstroke_run_production_recipe with recipeId character.sheet.create.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_create_game_asset_3d",
        "Shortcut for loomstroke_run_production_recipe with recipeId game_asset_3d.create.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_improve_game_asset_3d",
        "Shortcut for loomstroke_run_production_recipe with recipeId game_asset_3d.improve.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_get_production_lineage",
        "Return ProductionArtifact lineage from GenerationHistory and PCT metadata.",
        {
            "type": "object",
            "required": ["artifactId"],
            "properties": {"artifactId": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_run_visual_qa_preview",
        "Run read-only visual QA preview checks for an artifact or registered asset.",
        {
            "type": "object",
            "properties": {
                "artifactId": {"type": "string"},
                "assetId": {"type": "string"},
                "exportProfile": {"type": "object"},
                "bibleTerms": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_validate_export_profile",
        "Validate an export profile against MCP roots without writing files.",
        _GENERIC_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_preview_bible_injection",
        "Preview Bible store prompt injection fragments without external calls.",
        {
            "type": "object",
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"},
                "scopes": {"type": "array", "items": {"type": "string"}},
                "scopeIds": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_create_production_job",
        "Create a confirmed ProductionJob metadata record without leaking credentials.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_mark_artifact_decision",
        "Record a confirmed keep/reject/rework decision for a ProductionArtifact.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_add_bible_rule",
        "Add a confirmed Production Bible rule to the PCT store.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_create_export_preview",
        "Create a confirmed export preview, optionally writing a PNG under MCP roots.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_create_changeset",
        "Create a confirmed PCT changeset draft; no canvas mutation is performed.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_apply_changeset",
        "Apply a confirmed PCT changeset through supported operations.",
        {
            "type": "object",
            "required": ["changesetId"],
            "properties": {"changesetId": {"type": "string"}},
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_rollback_changeset",
        "Rollback a previously applied PCT changeset when supported inverse metadata exists.",
        {
            "type": "object",
            "required": ["changesetId"],
            "properties": {"changesetId": {"type": "string"}},
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_save_recipe",
        "Validate and save a credential-free .lsrecipe.json recipe in the PCT store.",
        _GENERIC_OBJECT_SCHEMA,
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_comfyui_object_info_summary",
        "Summarize ComfyUI object_info nodes, categories, models, and inferred model families.",
        {
            "type": "object",
            "properties": {
                "targetServerId": {"type": "string"},
                "forceRefresh": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        read_only=True,
        external=True,
    ),
    McpToolSpec(
        "loomstroke_comfyui_search_nodes",
        "Search ComfyUI object_info node schemas by text and optional inferred model family.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "modelFamily": {"type": "string"},
                "targetServerId": {"type": "string"},
                "forceRefresh": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        read_only=True,
        external=True,
    ),
    McpToolSpec(
        "loomstroke_comfyui_get_node_schema",
        "Return one ComfyUI node schema from object_info with normalized inputs and outputs.",
        {
            "type": "object",
            "required": ["classType"],
            "properties": {
                "classType": {"type": "string"},
                "includeRaw": {"type": "boolean", "default": True},
                "targetServerId": {"type": "string"},
                "forceRefresh": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        read_only=True,
        external=True,
    ),
    McpToolSpec(
        "loomstroke_comfyui_agent_catalog",
        "Return a JSON-only agent catalog derived from ComfyUI object_info roles and schemas.",
        {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "modelFamily": {"type": "string"},
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
                "includeNodes": {"type": "boolean", "default": True},
                "includeRaw": {"type": "boolean", "default": False},
                "targetServerId": {"type": "string"},
                "forceRefresh": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        read_only=True,
        external=True,
    ),
    McpToolSpec(
        "loomstroke_comfyui_nodes_by_role",
        "Return normalized ComfyUI nodes that match an agent workflow role.",
        {
            "type": "object",
            "required": ["role"],
            "properties": {
                "role": {
                    "type": "string",
                    "enum": [
                        "checkpoint_loader",
                        "model_loader",
                        "clip_loader",
                        "vae_loader",
                        "lora_loader",
                        "text_encoder",
                        "latent_source",
                        "sampler",
                        "decoder",
                        "image_input",
                        "image_output",
                        "controlnet",
                        "mask",
                        "upscaler",
                        "video",
                    ],
                },
                "modelFamily": {"type": "string"},
                "query": {"type": "string", "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                "includeRaw": {"type": "boolean", "default": False},
                "targetServerId": {"type": "string"},
                "forceRefresh": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        read_only=True,
        external=True,
    ),
    McpToolSpec(
        "loomstroke_comfyui_build_workflow_draft",
        "Build and validate a ComfyUI workflow draft from object_info using a model-family recipe.",
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["sdxl_t2i"], "default": "sdxl_t2i"},
                "modelFamily": {
                    "type": "string",
                    "enum": ["sdxl", "sd15", "flux", "sd3", "wan", "unknown"],
                    "default": "sdxl",
                },
                "positivePrompt": {"type": "string"},
                "negativePrompt": {"type": "string"},
                "checkpointName": {"type": "string"},
                "width": {"type": "integer", "minimum": 64, "maximum": 8192, "default": 1024},
                "height": {"type": "integer", "minimum": 64, "maximum": 8192, "default": 1024},
                "steps": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                "cfg": {"type": "number", "default": 7.0},
                "seed": {"type": "integer", "minimum": 0, "default": 0},
                "batchSize": {"type": "integer", "minimum": 1, "maximum": 16, "default": 1},
                "targetServerId": {"type": "string"},
                "forceRefresh": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        read_only=True,
        external=True,
    ),
    McpToolSpec(
        "loomstroke_apply_ai_effect_preset",
        "Instantiate an installed AI Effect preset as a canvas AI Effect layer.",
        {
            "type": "object",
            "required": ["presetId"],
            "properties": {
                "presetId": {"type": "string"},
                "waitForStateMs": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 5000,
                    "default": 500,
                },
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_get_ai_effect_layer_state",
        "Return an AI Effect layer's parameter schema, current values, slots, and gateway summary.",
        {
            "type": "object",
            "required": ["layerId"],
            "properties": {
                "layerId": {"type": "string"},
                "includeGatewayFile": {"type": "boolean", "default": False},
                "includeWorkflow": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_wait_layer_ready",
        "Wait until an AI Effect layer state is visible after preset application.",
        {
            "type": "object",
            "required": ["layerId"],
            "properties": {
                "layerId": {"type": "string"},
                "timeoutMs": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 30000,
                    "default": 5000,
                },
                "pollIntervalMs": {
                    "type": "integer",
                    "minimum": 25,
                    "maximum": 5000,
                    "default": 100,
                },
                "includeState": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_set_ai_effect_layer_state",
        "Update an AI Effect layer's parameters, input slots, output mode, or target server.",
        {
            "type": "object",
            "required": ["layerId"],
            "properties": {
                "layerId": {"type": "string"},
                "paramValues": {"type": "object"},
                "replaceParamValues": {"type": "boolean", "default": False},
                "slotRefs": {"type": "object"},
                "replaceSlotRefs": {"type": "boolean", "default": False},
                "paramUiState": {"type": "object"},
                "replaceParamUiState": {"type": "boolean", "default": False},
                "trigger": {"type": "string", "enum": ["manual", "on_change", "debounced"]},
                "targetServerId": {"type": ["string", "null"]},
                "outputModeOverride": {
                    "type": ["string", "null"],
                    "enum": ["self", "new_layer_above", None],
                },
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_export_asset",
        "Export a registered asset to a PNG path under allowed MCP roots.",
        {
            "type": "object",
            "required": ["assetId", "outputPath"],
            "properties": {
                "assetId": {"type": "string"},
                "outputPath": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_list_jobs",
        "List MCP-visible render, batch, and generation jobs.",
        _EMPTY_OBJECT_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_get_job_status",
        "Return status for one MCP-visible job.",
        {
            "type": "object",
            "required": ["jobId"],
            "properties": {"jobId": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_wait_job",
        "Wait for one MCP-visible job to reach a terminal status or timeout.",
        {
            "type": "object",
            "required": ["jobId"],
            "properties": {
                "jobId": {"type": "string"},
                "timeoutMs": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 300000,
                    "default": 30000,
                },
                "pollIntervalMs": {
                    "type": "integer",
                    "minimum": 25,
                    "maximum": 5000,
                    "default": 250,
                },
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_cancel_job",
        "Best-effort cancellation for a render, batch, or generation job.",
        {
            "type": "object",
            "required": ["jobId"],
            "properties": {"jobId": {"type": "string"}},
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_submit_generation",
        "Submit an AI Effect generation through an app-provided controller boundary.",
        {
            "type": "object",
            "required": ["layerId"],
            "properties": {
                "layerId": {"type": "string"},
                "forceRegenerate": {"type": "boolean", "default": False},
                "persistOverrides": {"type": "boolean", "default": True},
                "paramValues": {"type": "object"},
                "replaceParamValues": {"type": "boolean", "default": False},
                "slotRefs": {"type": "object"},
                "replaceSlotRefs": {"type": "boolean", "default": False},
                "paramUiState": {"type": "object"},
                "replaceParamUiState": {"type": "boolean", "default": False},
                "trigger": {"type": "string", "enum": ["manual", "on_change", "debounced"]},
                "targetServerId": {"type": ["string", "null"]},
                "outputModeOverride": {
                    "type": ["string", "null"],
                    "enum": ["self", "new_layer_above", None],
                },
                "applyToCanvas": {"type": "boolean", "default": True},
                "metadata": {"type": "object"},
            },
            "additionalProperties": True,
        },
        destructive=True,
        external=True,
    ),
    McpToolSpec(
        "loomstroke_create_asset_overlay_preview",
        (
            "Write a PNG preview by drawing text/rectangle overlays on a "
            "registered asset under MCP roots."
        ),
        {
            "type": "object",
            "required": ["assetId", "outputPath"],
            "properties": {
                "assetId": {"type": "string"},
                "outputPath": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
                "registerAsset": {"type": "boolean", "default": True},
                "overlays": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["type", "rect"],
                        "properties": {
                            "id": {"type": "string", "maxLength": 128},
                            "type": {"type": "string", "enum": ["text_box", "rect"]},
                            "rect": {
                                "type": "object",
                                "required": ["x", "y", "width", "height"],
                                "properties": {
                                    "x": {"type": "number"},
                                    "y": {"type": "number"},
                                    "width": {"type": "number"},
                                    "height": {"type": "number"},
                                },
                                "additionalProperties": False,
                            },
                            "text": {"type": "string", "maxLength": 2000},
                            "fontSize": {"type": "integer", "minimum": 6, "maximum": 256},
                            "minFontSize": {"type": "integer", "minimum": 6, "maximum": 256},
                            "fit": {
                                "type": "string",
                                "enum": ["none", "wrap", "shrink_to_fit", "truncate"],
                            },
                            "fitMode": {
                                "type": "string",
                                "enum": ["none", "wrap", "shrink_to_fit", "truncate"],
                            },
                            "radius": {"type": "number", "minimum": 0, "maximum": 200},
                            "strokeWidth": {"type": "number", "minimum": 0, "maximum": 64},
                            "safePadding": {"type": "number", "minimum": 0, "maximum": 512},
                            "padding": {"type": "number", "minimum": 0, "maximum": 512},
                            "textColor": {"type": "string"},
                            "fillColor": {"type": "string"},
                            "strokeColor": {"type": "string"},
                            "background": {"type": "string"},
                            "transparentTextBox": {"type": "boolean", "default": True},
                        },
                        "additionalProperties": False,
                    },
                    "default": [],
                },
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_create_speech_bubble_overlay",
        (
            "Write a PNG preview by placing manga-style speech bubbles and fitted text "
            "on a registered asset under MCP roots."
        ),
        {
            "type": "object",
            "required": ["assetId", "outputPath", "bubbles"],
            "properties": {
                "assetId": {"type": "string"},
                "outputPath": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
                "registerAsset": {"type": "boolean", "default": True},
                "bubbles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["text", "anchor"],
                        "properties": {
                            "id": {"type": "string", "maxLength": 128},
                            "text": {"type": "string", "maxLength": 2000},
                            "anchor": {
                                "type": "object",
                                "required": ["x", "y"],
                                "properties": {
                                    "x": {"type": "number"},
                                    "y": {"type": "number"},
                                },
                                "additionalProperties": False,
                            },
                            "maxWidth": {"type": "number", "minimum": 0},
                            "style": {
                                "type": "string",
                                "enum": ["manga_round", "manga_rect", "caption"],
                                "default": "manga_round",
                            },
                            "tailDirection": {
                                "type": "string",
                                "enum": ["left", "right", "up", "down", "none"],
                                "default": "none",
                            },
                            "fontSize": {"type": "integer", "minimum": 6, "maximum": 256},
                            "minFontSize": {"type": "integer", "minimum": 6, "maximum": 256},
                            "fit": {
                                "type": "string",
                                "enum": ["none", "wrap", "shrink_to_fit", "truncate"],
                            },
                            "fitMode": {
                                "type": "string",
                                "enum": ["none", "wrap", "shrink_to_fit", "truncate"],
                            },
                            "safePadding": {"type": "number", "minimum": 2, "maximum": 512},
                            "padding": {"type": "number", "minimum": 2, "maximum": 512},
                            "radius": {"type": "number", "minimum": 0, "maximum": 200},
                            "strokeWidth": {"type": "number", "minimum": 0, "maximum": 64},
                            "textColor": {"type": "string"},
                            "fillColor": {"type": "string"},
                            "strokeColor": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                },
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
    McpToolSpec(
        "loomstroke_batch_render",
        "Render pages/scenes to PNG files under allowed MCP roots using optional wgpu.",
        {
            "type": "object",
            "required": ["outputDirectory"],
            "properties": {
                "outputDirectory": {"type": "string"},
                "pageIds": {"type": "array", "items": {"type": "string"}},
                "includeRoles": {"type": "array", "items": {"type": "string"}},
                "excludeRoles": {"type": "array", "items": {"type": "string"}},
                "fileNamePattern": {"type": "string", "default": "{page_id}.png"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        destructive=True,
    ),
)


TOOLS = (*TOOLS, *editing_tools.TOOLS)

# Canonical AI Select tool names (Phase E). The implementation is
# model-agnostic (the AI Selection provider runs the SAM 3.1 backend), so the
# official API hides the model generation: every ``loomstroke_sam3_*`` tool is
# also published under the canonical ``loomstroke_ai_select_*`` name. The
# legacy names stay callable as deprecated aliases so existing automations and
# agent workflows keep working unchanged.
_LEGACY_TOOL_PREFIX = "loomstroke_sam3_"
_CANONICAL_TOOL_PREFIX = "loomstroke_ai_select_"


def _canonical_tool_name(legacy_name: str) -> str:
    return _CANONICAL_TOOL_PREFIX + legacy_name[len(_LEGACY_TOOL_PREFIX) :]


CANONICAL_ALIAS_TOOLS: tuple[McpToolSpec, ...] = tuple(
    replace(
        tool,
        name=_canonical_tool_name(tool.name),
        description=tool.description + f" (canonical name; also callable as legacy {tool.name})",
    )
    for tool in TOOLS
    if tool.name.startswith(_LEGACY_TOOL_PREFIX)
)

_LEGACY_NAME_BY_CANONICAL: dict[str, str] = {
    _canonical_tool_name(tool.name): tool.name
    for tool in TOOLS
    if tool.name.startswith(_LEGACY_TOOL_PREFIX)
}


def resolve_canonical_tool_name(name: str) -> str:
    """Map a canonical ``loomstroke_ai_select_*`` name to its dispatch name."""

    return _LEGACY_NAME_BY_CANONICAL.get(name, name)


TOOLS = (*TOOLS, *PANEL_TOOLS)


def list_tools() -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    for tool in TOOLS:
        spec = tool
        if spec.name.startswith(_LEGACY_TOOL_PREFIX):
            spec = replace(
                spec,
                description=(
                    f"[Deprecated: legacy alias of {_canonical_tool_name(spec.name)}] "
                    + spec.description
                ),
            )
        tools.append(spec.to_protocol())
    tools.extend(tool.to_protocol() for tool in CANONICAL_ALIAS_TOOLS)
    return {"tools": tools}


async def call_tool(  # noqa: PLR0911, PLR0912, PLR0915
    service: Any,
    name: str,
    arguments: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if name in PANEL_TOOL_BY_NAME:
        return _tool_result(await dispatch_panel_tool(service, name, dict(arguments or {})))
    args = dict(arguments or {})
    name = resolve_canonical_tool_name(name)
    editing_tools.guard_existing_edit(service, name, args)
    if editing_tools.handles(name):
        return _tool_result(await editing_tools.dispatch(service, name, args))
    if name == "loomstroke_get_capabilities":
        return _tool_result(service.get_capabilities())
    if name == "loomstroke_sam3_get_readiness":
        return _tool_result(await service.get_sam3_readiness_resource())
    if name == "loomstroke_sam3_segment_asset":
        return _tool_result(await service.segment_sam3_asset(args))
    if name == "loomstroke_sam3_segment_current":
        return _tool_result(await service.segment_current_sam3(args))
    if name == "loomstroke_sam3_cancel_job":
        return _tool_result(await service.cancel_sam3_job(_required(args, "jobId")))
    if name == "loomstroke_sam3_create_overlay_preview":
        return _tool_result(await service.create_sam3_overlay_preview(args))
    if name == "loomstroke_sam3_apply_candidate_as_selection":
        return _tool_result(await service.apply_sam3_candidate_as_selection(args))
    if name == "loomstroke_sam3_apply_candidate_as_layer_mask":
        return _tool_result(await service.apply_sam3_candidate_as_layer_mask(args))
    if name == "loomstroke_sam3_export_mask_package":
        return _tool_result(await service.export_sam3_mask_package(args))
    if name == "loomstroke_sam3_get_source_snapshot":
        source_snapshot_id = quote(_required(args, "sourceSnapshotId"), safe="")
        return _tool_result(
            service.read_sam3_resource(f"loomstroke://sam3/source-snapshot/{source_snapshot_id}")
        )
    if name == "loomstroke_sam3_get_candidate_set":
        candidate_set_id = quote(_required(args, "candidateSetId"), safe="")
        return _tool_result(
            service.read_sam3_resource(f"loomstroke://sam3/candidate-set/{candidate_set_id}")
        )
    if name == "loomstroke_sam3_get_candidate":
        candidate_id = quote(_required(args, "candidateId"), safe="")
        return _tool_result(
            service.read_sam3_resource(f"loomstroke://sam3/candidate/{candidate_id}")
        )
    if name == "loomstroke_sam3_get_mask_asset":
        mask_asset_id = quote(_required(args, "maskAssetId"), safe="")
        return _tool_result(
            service.read_sam3_resource(f"loomstroke://sam3/mask-asset/{mask_asset_id}")
        )
    if name == "loomstroke_sam3_get_preview_asset":
        preview_asset_id = quote(_required(args, "previewAssetId"), safe="")
        return _tool_result(
            service.read_sam3_resource(f"loomstroke://sam3/preview-asset/{preview_asset_id}")
        )
    if name == "loomstroke_sam3_get_job_status":
        job_id = quote(_required(args, "jobId"), safe="")
        return _tool_result(service.read_sam3_resource(f"loomstroke://sam3/job/{job_id}"))
    if name == "loomstroke_list_documents":
        return _tool_result({"documents": service.list_documents()})
    if name == "loomstroke_get_scene_snapshot":
        return _tool_result(service.get_scene_snapshot())
    if name == "loomstroke_graphic_plan":
        return _tool_result(service.graphic_plan(args))
    if name == "loomstroke_get_edit_context":
        return _tool_result(service.get_edit_context(args))
    if name == "loomstroke_plan_canvas_edit":
        return _tool_result(service.plan_canvas_edit(args))
    if name == "loomstroke_create_canvas_changeset":
        return _tool_result(service.create_canvas_changeset(args))
    if name == "loomstroke_preview_canvas_changeset":
        return _tool_result(service.preview_canvas_changeset(args))
    if name == "loomstroke_answer_canvas_clarification":
        return _tool_result(service.answer_canvas_clarification(args))
    if name == "loomstroke_apply_canvas_changeset":
        return _tool_result(await service.apply_canvas_changeset(args))
    if name == "loomstroke_verify_canvas_edit":
        return _tool_result(service.verify_canvas_edit(args))
    if name == "loomstroke_create":
        return _tool_result(await service.create_from_request(args))
    if name == "loomstroke_validate_comic_plan":
        return _tool_result(service.validate_comic_plan(args))
    if name == "loomstroke_plan_comic_page":
        return _tool_result(service.plan_comic_page(args))
    if name == "loomstroke_create_comic_page":
        return _tool_result(await service.create_comic_page(args))
    if name == "loomstroke_render_comic_preview":
        return _tool_result(await service.render_comic_preview(args))
    if name == "loomstroke_export_comic_package":
        return _tool_result(await service.export_comic_package(args))
    if name == "loomstroke_create_comic_changeset":
        return _tool_result(await service.create_comic_changeset(args))
    if name == "loomstroke_render_preview":
        return _tool_result(service.render_preview(args))
    if name == "loomstroke_list_assets":
        return _tool_result({"assets": service.list_assets()})
    if name == "loomstroke_import_asset":
        return _tool_result(service.import_asset(args))
    if name == "loomstroke_list_ai_effect_presets":
        return _tool_result(await service.list_ai_effect_presets())
    if name == "loomstroke_get_feature_catalog":
        return _tool_result(await service.get_feature_catalog(args))
    if name == "loomstroke_explain_feature":
        return _tool_result(await service.explain_feature(args))
    if name == "loomstroke_get_ai_effect_catalog":
        return _tool_result(await service.get_ai_effect_catalog(args))
    if name == "loomstroke_get_ai_effect_preset_detail":
        return _tool_result(await service.get_ai_effect_preset_detail(args))
    if name == "loomstroke_list_production_recipes":
        return _tool_result(service.list_production_recipes(args))
    if name == "loomstroke_get_production_recipe_detail":
        return _tool_result(service.get_production_recipe_detail(args))
    if name == "loomstroke_validate_production_recipe_input":
        return _tool_result(service.validate_production_recipe_input(args))
    if name == "loomstroke_plan_production_job":
        return _tool_result(service.plan_production_job(args))
    if name == "loomstroke_run_production_recipe":
        return _tool_result(await service.run_production_recipe(args))
    if name == "loomstroke_score_artifact_candidates":
        return _tool_result(service.score_artifact_candidates(args))
    if name == "loomstroke_create_rework_plan":
        return _tool_result(service.create_rework_plan(args))
    if name == "loomstroke_export_production_package":
        return _tool_result(await service.export_production_package(args))
    if name in _PRODUCTION_RECIPE_SHORTCUTS:
        shortcut_args = {**args, "recipeId": _PRODUCTION_RECIPE_SHORTCUTS[name]}
        return _tool_result(await service.run_production_recipe(shortcut_args))
    if name == "loomstroke_get_production_lineage":
        return _tool_result(await service.get_production_lineage(args))
    if name == "loomstroke_run_visual_qa_preview":
        return _tool_result(await service.run_visual_qa_preview(args))
    if name == "loomstroke_validate_export_profile":
        return _tool_result(await service.validate_export_profile(args))
    if name == "loomstroke_preview_bible_injection":
        return _tool_result(await service.preview_bible_injection(args))
    if name == "loomstroke_create_production_job":
        return _tool_result(await service.create_production_job(args))
    if name == "loomstroke_mark_artifact_decision":
        return _tool_result(await service.mark_artifact_decision(args))
    if name == "loomstroke_add_bible_rule":
        return _tool_result(await service.add_bible_rule(args))
    if name == "loomstroke_create_export_preview":
        return _tool_result(await service.create_export_preview(args))
    if name == "loomstroke_create_changeset":
        return _tool_result(await service.create_changeset(args))
    if name == "loomstroke_apply_changeset":
        return _tool_result(await service.apply_changeset(args))
    if name == "loomstroke_rollback_changeset":
        return _tool_result(await service.rollback_changeset(args))
    if name == "loomstroke_save_recipe":
        return _tool_result(await service.save_recipe(args))
    if name == "loomstroke_comfyui_object_info_summary":
        return _tool_result(await service.comfyui_object_info_summary(args))
    if name == "loomstroke_comfyui_search_nodes":
        return _tool_result(await service.comfyui_search_nodes(args))
    if name == "loomstroke_comfyui_get_node_schema":
        return _tool_result(await service.comfyui_get_node_schema(args))
    if name == "loomstroke_comfyui_agent_catalog":
        return _tool_result(await service.comfyui_agent_catalog(args))
    if name == "loomstroke_comfyui_nodes_by_role":
        return _tool_result(await service.comfyui_nodes_by_role(args))
    if name == "loomstroke_comfyui_build_workflow_draft":
        return _tool_result(await service.comfyui_build_workflow_draft(args))
    if name == "loomstroke_apply_ai_effect_preset":
        return _tool_result(await service.apply_ai_effect_preset(args))
    if name == "loomstroke_get_ai_effect_layer_state":
        return _tool_result(service.get_ai_effect_layer_state(args))
    if name == "loomstroke_wait_layer_ready":
        return _tool_result(await service.wait_layer_ready(args))
    if name == "loomstroke_set_ai_effect_layer_state":
        return _tool_result(await service.set_ai_effect_layer_state(args))
    if name == "loomstroke_export_asset":
        return _tool_result(await service.export_asset(args))
    if name == "loomstroke_list_jobs":
        return _tool_result({"jobs": service.list_jobs()})
    if name == "loomstroke_get_job_status":
        return _tool_result(service.get_job_status(_required(args, "jobId")))
    if name == "loomstroke_wait_job":
        return _tool_result(await service.wait_job(args))
    if name == "loomstroke_cancel_job":
        return _tool_result(await service.cancel_job(_required(args, "jobId")))
    if name == "loomstroke_submit_generation":
        return _tool_result(await service.submit_generation(args))
    if name == "loomstroke_create_asset_overlay_preview":
        return _tool_result(await service.create_asset_overlay_preview(args))
    if name == "loomstroke_create_speech_bubble_overlay":
        return _tool_result(await service.create_speech_bubble_overlay(args))
    if name == "loomstroke_batch_render":
        return _tool_result(await service.batch_render(args))
    raise McpStructuredError(
        ReasonCode.NOT_FOUND,
        "Tool not found.",
        details={"tool": name},
        code=-32601,
    )


def _tool_result(value: Any) -> dict[str, Any]:
    return {
        "content": [json_text_content(value)],
        "structuredContent": value,
        "isError": False,
    }


def _required(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            f"'{key}' is required.",
            details={"argument": key},
            code=-32602,
        )
    return value.strip()


_PRODUCTION_RECIPE_SHORTCUTS = {
    "loomstroke_improve_manga_page": "comic_page.improve",
    "loomstroke_create_character_design": "character.design.create",
    "loomstroke_create_character_sheet": "character.sheet.create",
    "loomstroke_create_game_asset_3d": "game_asset_3d.create",
    "loomstroke_improve_game_asset_3d": "game_asset_3d.improve",
}
