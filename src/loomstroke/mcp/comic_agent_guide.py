"""Client-facing instructions for the existing comic MCP workflow."""

from __future__ import annotations

from typing import Any


def comic_agent_guide() -> dict[str, Any]:
    """Return a fresh JSON guide without accessing models, documents, or services."""
    script = (
        "Panel 1: In a park, a cat looks up at the sky. \u201cIt is sunny today.\u201d\n"
        "Panel 2: The cat puts a small umbrella in a bag. \u201cBetter bring an umbrella.\u201d\n"
        "Panel 3: Rain starts on the way home. \u201cSuddenly, rain!\u201d\n"
        "Panel 4: The cat opens the umbrella and smiles. \u201cGood thing I came prepared.\u201d"
    )
    planning_arguments = {
        "script": script,
        "page": {"width": 832, "height": 1216, "dpi": 72},
        "constraints": {"maxPanels": 4, "safeMargin": 40, "minimumFontSize": 12},
    }
    return {
        "preferredCreationTool": "loomstroke_create_comic_page",
        "goal": "Turn a comic request into a script, layout, artwork, text, preview, "
        "edits, and export.",
        "start": {
            "tools": ["loomstroke_get_capabilities", "loomstroke_get_scene_snapshot"],
            "documentTools": ["loomstroke_new_document", "loomstroke_open_document"],
            "instructions": [
                "First inspect available tools, readiness, the confirmation mechanism, and the "
                "current document.",
                "Determine whether the request requires only a preview, a finished image with "
                "artwork, or an editable document.",
                "Before editing an existing document, identify the target page and layers. "
                "Do not assume permission to discard or replace the document.",
                "If a new document is needed, pass the page's width, height, and dpi to "
                "new_document. Open an existing .lstroke with an allowed-root path through "
                "open_document. Save unsaved changes first; do not invent discardChanges consent.",
                "For multiple pages, repeat this workflow for each page. A single comic creation "
                "call does not automatically create a book or all its pages.",
            ],
        },
        "script": {
            "instructions": [
                "Do not pass only 'make a comic'. Develop scenes and dialogue that match the "
                "request, using the user's script when provided.",
                "Start each panel on a new line with Panel 1:, Panel 2:, and so on. Japanese "
                "panel markers are also accepted. Enclose balloon dialogue in curly double "
                "quotes (U+201C/U+201D), leaving scene descriptions outside the quotes. Japanese "
                "corner brackets are also accepted; straight ASCII double quotes are not parsed "
                "as quoted dialogue.",
                "Write four panel blocks for a four-panel comic. constraints.maxPanels is an "
                "upper bound: setting it to 4 does not invent missing panels or story content. "
                "The default is 4 and the allowed range is 1 to 8.",
                "Keep script within 8000 characters. Scene descriptions retain at most 120 "
                "characters per panel, so keep detailed artwork prompts separately.",
                "The generic loomstroke_create router uses the original request as literal "
                "script and does not develop a story. It currently does not forward a separate "
                "script/story argument to the comic route. Pass the prepared script directly to "
                "loomstroke_create_comic_page or loomstroke_plan_comic_page.",
            ],
        },
        "quickPreview": {
            "instructions": [
                "Use the example below for a quick script-to-preview call. Omit outputPath to "
                "create a registered PNG asset without writing a file.",
                "For a finished image with artwork, first prepare assets using the artwork "
                "workflow, then add assetIds in panel order to the same call. Without image "
                "assets, art slots are placeholders.",
                "This call alone does not create or save a live canvas or a .lstroke document.",
            ],
            "example": {
                "tool": "loomstroke_create_comic_page",
                "arguments": {**planning_arguments, "registerAsset": True},
            },
            "resultFields": [
                "structuredContent.plan",
                "structuredContent.validation",
                "structuredContent.preview.assetId",
                "structuredContent.preview.textRendered",
                "structuredContent.blocked",
            ],
            "genericRouterResult": {
                "tool": "loomstroke_create",
                "assetIdField": "structuredContent.result.preview.assetId",
            },
        },
        "layout": {
            "example": {"tool": "loomstroke_plan_comic_page", "arguments": planning_arguments},
            "steps": [
                {
                    "tool": "loomstroke_plan_comic_page",
                    "instructions": "Pass script and any planning options; "
                    "retain structuredContent.plan.",
                },
                {
                    "tool": "loomstroke_validate_comic_plan",
                    "instructions": "Pass the retained plan as plan. Inspect ok, blocked, "
                    "blockedReasons, layoutWarnings, and textFitReports for out-of-bounds "
                    "geometry and text fit.",
                },
                {
                    "tool": "loomstroke_render_comic_preview",
                    "instructions": "Pass the validated plan and registerAsset:true. The result "
                    "contains structuredContent.assetId. Follow previewInspection to check "
                    "text and artwork.",
                },
            ],
            "instructions": [
                "The default layout is one vertical column of equally sized panels. "
                "style.readingDirection is metadata; setting rtl does not automatically "
                "rearrange panels into a right-to-left layout.",
                "For a custom layout, adjust rect, artSlots, balloons, captions, and textStyle "
                "inside the returned plan's panels, then validate and render again.",
                "A valid plan does not guarantee that text was actually rendered.",
            ],
        },
        "artwork": {
            "instructions": [
                "style.description is art-direction metadata and does not generate artwork. "
                "Prepare separate generation prompts for each panel's scene, keeping characters "
                "and style consistent.",
                "Find existing registered images with loomstroke_list_assets. Register local "
                "PNG/JPEG/WebP files under allowed roots with loomstroke_import_asset.",
                "Generation requires a compatible preset applier, generation submitter, "
                "backend, and confirmation mechanism. External communication must obey "
                "privacy/allow_external_services.",
            ],
            "existingImageTools": ["loomstroke_list_assets", "loomstroke_import_asset"],
            "generationSteps": [
                {
                    "tool": "loomstroke_list_ai_effect_presets",
                    "instructions": "Find available image-generation presets. "
                    "Do not invent presetId.",
                },
                {
                    "tool": "loomstroke_get_ai_effect_preset_detail",
                    "instructions": "Inspect the candidate presetId's inputs "
                    "and supported capabilities.",
                },
                {
                    "tool": "loomstroke_apply_ai_effect_preset",
                    "instructions": "Apply the selected presetId and retain the returned "
                    "layerId. This changes the document; obey the confirmation policy.",
                },
                {
                    "tool": "loomstroke_get_ai_effect_layer_state",
                    "instructions": "Inspect layerId's actual parameter schema and input "
                    "slots. Do not assume a fixed parameter named prompt or invent inputs.",
                },
                {
                    "tool": "loomstroke_set_ai_effect_layer_state",
                    "instructions": "Set layerId, paramValues matching the actual schema, "
                    "and any required slotRefs.",
                },
                {
                    "tool": "loomstroke_submit_generation",
                    "instructions": "Submit asset generation with layerId and "
                    "applyToCanvas:false; retain jobId.",
                },
                {
                    "tool": "loomstroke_wait_job",
                    "instructions": "Pass jobId and inspect structuredContent.status and "
                    "structuredContent.resultAssetIds. A wait timeout does not mean "
                    "completion. Do not report artwork as created after failure or cancellation.",
                },
            ],
            "attach": "Put the resulting registered asset IDs into assetIds in panel order, "
            "then recreate the comic plan and preview. The first image maps to the first panel, "
            "the second to the second panel. Missing images are not automatically duplicated.",
        },
        "previewInspection": {
            "assetResourceContainsPixels": False,
            "exportTool": "loomstroke_export_asset",
            "instructions": [
                "Check textRendered, textRenderer, textFitReports, and warnings. When "
                "textRendered:false, do not report a finished preview containing rendered text.",
                "loomstroke://asset/{assetId} returns metadata JSON, not image pixels. "
                "Asset-only results do not include an inline image either.",
                "For visual inspection, output a PNG to an outputPath under allowed roots, "
                "or pass assetId, outputPath, and overwrite:false to loomstroke_export_asset. "
                "Obey the configured confirmation policy and use the client's image viewer.",
                "If image pixels cannot be viewed, report assetId and validation results "
                "without claiming visual verification.",
            ],
        },
        "editable": {
            "requiresConfirmationCallback": True,
            "requiresLiveCanvas": True,
            "liveCanvasMeaning": "An actual canvas connected to an open document is required; "
            "the mode name alone does not determine availability.",
            "includesArtworkPixels": False,
            "steps": [
                {
                    "tool": "loomstroke_get_scene_snapshot",
                    "instructions": "Compare the target document, page, and canvas dimensions "
                    "with the plan. Do not assume applying a changeset resizes the canvas.",
                },
                {
                    "tool": "loomstroke_create_comic_changeset",
                    "instructions": "Pass the validated, previewed plan and an optional name. "
                    "Draft creation also requires a confirmation callback. Inspect data.summary "
                    "and data.changeset.operations, then retain "
                    "structuredContent.data.changeset.changesetId.",
                },
                {
                    "tool": "loomstroke_apply_changeset",
                    "instructions": "Pass the retained ID as changesetId, with no other "
                    "arguments. Applying also requires a confirmation callback and a canvas "
                    "connected to an open document. Do not add confirmed:true or approvalTicket.",
                },
                {
                    "tool": "loomstroke_get_scene_snapshot",
                    "instructions": "Recheck the resulting layers and text after applying.",
                },
            ],
            "instructions": [
                "Comic PCT changesets and Canvas Assistant CanvasChangesets have different "
                "contracts. Do not pass a Comic draft to preview_canvas_changeset or "
                "apply_canvas_changeset.",
                "Editable apply creates panel borders, balloons, text, and art placeholders "
                "as vector/text objects. It does not place artwork pixels from assetIds and "
                "reports raster_art_slots_preview_only. Distinguish an illustrated PNG or "
                "package from a complete editable page containing artwork.",
                "Placing artwork pixels into the live document requires separate raster-layer "
                "placement. This comic changeset does not support automatic artwork placement "
                "or panel-local masking.",
                "To revert, inspect history and use normal Undo. rollback_changeset is not "
                "available for Comic changesets.",
            ],
            "historyTools": ["loomstroke_get_history_state", "loomstroke_undo", "loomstroke_redo"],
        },
        "saveAndExport": {
            "tools": ["loomstroke_export_comic_package", "loomstroke_save_document"],
            "instructions": [
                "To deliver a PNG and its plan together, pass plan, outputDirectory, an "
                "optional fileName, and overwrite:false to export_comic_package. The output "
                "directory must be under configured MCP roots; obey the confirmation policy.",
                "The package contains a PNG, comic_page_plan.json, text_fit_report.json, and "
                "package_manifest.json. Check the returned packageDirectory and files. "
                "The package is not a .lstroke document.",
                "Save edits applied to the live document separately with "
                "loomstroke_save_document. For Save As, pass a .lstroke path under allowed roots "
                "and overwrite:false. A preview plan alone cannot be saved as a .lstroke document.",
                "For blocked:true, report any created preview.assetId or previewFallback.assetId "
                "and identify incomplete export, apply, or backend steps. Use an asset-only "
                "preview if the write policy requires confirmation but no callback is available. "
                "Headless export can work when the policy does not require confirmation; "
                "judge success using actual files, fileWrite, and blocked results. Comic draft "
                "and apply always require a confirmation callback. Apply also requires a "
                "connected canvas and an open document; check these conditions even in headless "
                "mode rather than deciding from the mode name.",
            ],
        },
        "nativePanelLimitations": {
            "connected": False,
            "panels": ["Comic Tools", "Comic Assets", "Comic Project", "Comic Workflow"],
            "instructions": "This guide describes the comic plan, PNG, and Comic PCT changeset "
            "paths. Native Comic panel asset management, project management, and progress "
            "tracking are not connected. MCP vector metadata is not guaranteed to make "
            "generated objects editable by the native Comic controller.",
        },
    }
