"""ToolPlugin shell for the future SAM3 AI Select workflow."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, unquote
from uuid import uuid4

from ...ai_selection import (
    AI_SELECT_RUNTIME_MODE_SAM31,
    SAM3_AI_SELECT_COMBINE_MODE_VALUES,
    SAM3_AI_SELECT_EXPORT_SOURCE_VALUES,
    SAM3_AI_SELECT_MODE_VALUES,
    SAM3_AI_SELECT_OPERATION_VALUES,
    SAM3_AI_SELECT_PREVIEW_COLOR_MODE_VALUES,
    SAM3_AI_SELECT_SCOPE_VALUES,
    SAM3_AI_SELECT_SHEET_FORMAT_VALUES,
    SAM3_MANGA_MASK_ASSISTANT_FILTER_RESULT_SCHEMA_VERSION,
    SAM3_MANGA_MASK_ASSISTANT_TABLE_COLUMNS,
    SAM3_MANGA_MASK_ASSISTANT_TABLE_MODEL_SCHEMA_VERSION,
    SAM3_MANGA_MASK_PRESET_VALUES,
    SAM3_SELECTION_APPLY_PLAN_SCHEMA_VERSION,
    SAM3_SELECTION_REFINE_DEFAULT_OPTIONS,
    BoxPrompt,
    GenerationSubmitter,
    MangaMaskPreset,
    MaskCandidate,
    MaskCombineMode,
    MaskRefineOptions,
    PointLabel,
    PointPrompt,
    Sam3AIEffectPreviewRequestError,
    Sam3AIEffectSlotBindingError,
    Sam3AiSelectImageRunResult,
    Sam3AiSelectSourceImageError,
    Sam3AiSelectToolSettings,
    Sam3AssetStore,
    Sam3CandidateOverlayItem,
    Sam3ComicMaskPackage,
    Sam3ComicMaskPackageError,
    Sam3ComicMaskPackageExportError,
    Sam3CutToLayerAdapterError,
    Sam3ExtractLayerAdapterError,
    Sam3ExtractLayerMode,
    Sam3GenerationMaskPrepareError,
    Sam3GenerationMaskQAError,
    Sam3GenerationResultDiffError,
    Sam3GenerationResultDiffQA,
    Sam3GenerationResultDiffQAError,
    Sam3LayerMaskAdapterError,
    Sam3LayerMaskCommandAdapterError,
    Sam3MangaInpaintPreparation,
    Sam3MangaInpaintPreviewHistoryMetadataError,
    Sam3MangaInpaintPreviewRequestError,
    Sam3MangaMaskScorecardError,
    Sam3MangaMaskScorecardProjectionError,
    Sam3MaskCombineAdapterError,
    Sam3MaskCombineError,
    Sam3MaskProvider,
    Sam3MaskRefineAdapterError,
    Sam3ProviderBinding,
    Sam3SelectionApplyExecutionResult,
    Sam3SelectionApplyPlan,
    Sam3SelectionBridgeError,
    Sam3SelectionCanvasSubmitter,
    Sam3SelectionOverlayModel,
    Sam3SelectionRefineResult,
    Sam3SelectionSession,
    Sam3SelectionSessionError,
    Sam3SpeechBubbleProtectMaskAssetError,
    ai_select_runtime_mode,
    analyze_generation_mask_asset,
    analyze_generation_result_diff_mask,
    analyze_preview_apply_readiness,
    apply_selection_plan,
    apply_selection_session,
    build_ai_effect_mask_slot_binding_command,
    build_ai_effect_preview_generation_request,
    build_candidate_group_to_layer_group_command,
    build_clipping_mask_apply_command,
    build_comic_mask_package_from_candidate_set,
    build_cut_to_new_layer_command,
    build_extract_to_new_layer_command,
    build_layer_mask_add_candidate_command,
    build_layer_mask_apply_command,
    build_layer_mask_image_from_asset,
    build_manga_inpaint_preparation,
    build_manga_inpaint_preview_history_metadata,
    build_manga_inpaint_preview_request,
    build_manga_mask_assistant_action_request,
    build_manga_mask_assistant_table_model,
    build_manga_mask_assistant_view_model,
    build_manga_mask_scorecard_production_projection,
    build_manga_mask_selection_apply_plan,
    build_protect_mask_selection_apply_plan,
    build_selection_overlay_model,
    build_session_apply_plan,
    build_speech_bubble_protect_plan,
    combine_mask_assets,
    combine_speech_bubble_protect_mask_assets,
    create_background_generation_mask_asset,
    create_configured_sam3_worker_client,
    create_selection_inpaint_mask_asset,
    export_comic_mask_package,
    filter_manga_mask_assistant_view_model,
    normalize_mask_combine_mode,
    parse_manga_mask_assistant_action_id,
    refine_mask_asset,
    refine_selection_candidate,
    reject_session_candidate,
    resolve_ai_select_source_image,
    resolve_sam3_provider_binding,
    run_ai_select_from_qimage,
    run_generation_result_diff_qa,
    save_speech_bubble_protect_mask_asset,
    score_comic_mask_package_candidates,
    select_next_session_candidate,
    select_previous_session_candidate,
    select_session_candidate,
    submit_ai_effect_preview_generation_request,
    submit_manga_inpaint_preview_request,
    with_comic_mask_candidate_acceptance,
)
from ...ai_selection.asset_export import (
    Sam3AssetExportError,
    Sam3AssetExportWriteError,
    SpriteSheetFrame,
    build_selection_export_image,
    build_sprite_atlas_payload,
    compose_sprite_sheet_image,
    encode_sprite_sheet_image,
    encode_transparent_png,
    pack_sprite_sheet,
    sheet_max_side_px,
    trim_image_to_alpha_bbox,
    write_export_batch,
    write_export_file,
)
from ...ai_selection.cut_to_layer_adapter import build_masked_layer_image
from ...ai_selection.sam3_async_submit_state import Sam3AsyncSubmitState
from ...ai_selection.sam3_panel_geometry_provider import Sam3PanelGeometryProvider
from ...ai_selection.sam3_submit_cancel_bridge import Sam3SubmitCancelBridge
from ...core.generation_record import GenerationRecord
from ...plugins.sdk.base import PluginMetadata, PluginType
from ...plugins.sdk.tool import (
    ContextToolbarSpec,
    ContextWidgetSpec,
    PropertySpec,
    PropertyType,
    ToolDescriptor,
    ToolMetadata,
    ToolSettings,
)
from ...ui.translations import translated_literal_domain
from .candidate_list_model import build_candidate_list_view_model
from .overlay_painter import Sam3AiSelectOverlayPainter

if TYPE_CHECKING:
    from ...core.commands import Command
    from ...host_api import HostAPI, StrokePoint

logger = logging.getLogger(__name__)

PLUGIN_ID = "loomstroke.sam3_ai_select"
TOOL_ID = "sam3_ai_select"
CANDIDATE_LIST_PANEL_ID = "ui.sam3_ai_select_candidates"
MANGA_MASK_ASSISTANT_PANEL_ID = "ui.sam3_ai_select_manga_mask_assistant"
_TRANSLATION_DOMAIN = "SAM3AISelect"
_TOOL_ORDER = 13
_SAFE_SAM3_ASSET_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_TOOL_STATE_SCHEMA_VERSION = "sam3.ai_select_tool_state.v1"
_CANDIDATE_LIST_SCHEMA_VERSION = "sam3.ai_select_candidate_list.v1"
_CONTEXT_ACTIONS_SCHEMA_VERSION = "sam3.ai_select_context_actions.v1"
_BOUND_CONTEXT_ACTIONS_SCHEMA_VERSION = "sam3.ai_select_bound_context_actions.v1"
_CONTEXT_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_context_action_result.v1"
_RUN_READINESS_SCHEMA_VERSION = "sam3.ai_select_run_readiness.v1"
_BOUND_RUN_READINESS_SCHEMA_VERSION = "sam3.ai_select_bound_run_readiness.v1"
_RUN_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_run_action_result.v1"
_REFINE_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_refine_action_result.v1"
_LAYER_MASK_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_layer_mask_action_result.v1"
_CLIPPING_MASK_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_clipping_mask_action_result.v1"
_CUT_TO_LAYER_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_cut_to_layer_action_result.v1"
_ASSET_EXPORT_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_asset_export_action_result.v1"
_EXTRACT_TO_LAYER_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_extract_to_layer_action_result.v1"
_SAVE_MASK_ASSET_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_save_mask_asset_action_result.v1"
_AI_EFFECT_MASK_SLOT_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_ai_effect_mask_slot_action_result.v1"
)
_AI_EFFECT_PREVIEW_REQUEST_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_ai_effect_preview_request_action_result.v1"
)
_AI_EFFECT_PREVIEW_SUBMIT_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_ai_effect_preview_submit_action_result.v1"
)
_PREVIEW_APPLY_READINESS_HISTORY_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_preview_apply_readiness_history_action_result.v1"
)
_PREVIEW_OUTPUT_IMAGE_PATH_HISTORY_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_preview_output_image_path_history_action_result.v1"
)
_PREVIEW_APPLY_HISTORY_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_preview_apply_history_action_result.v1"
)
_GENERATION_DIFF_QA_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_generation_diff_qa_action_result.v1"
)
_GENERATION_DIFF_QA_HISTORY_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_generation_diff_qa_history_action_result.v1"
)
_COMBINE_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_combine_action_result.v1"
_MASK_REFINE_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_mask_refine_action_result.v1"
_CANDIDATE_GROUP_TO_LAYER_GROUP_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.ai_select_candidate_group_to_layer_group_action_result.v1"
)
_MANGA_MASK_ASSISTANT_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_tool_action_result.v1"
)
_MANGA_MASK_ASSISTANT_PACKAGE_BUILD_RESULT_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_package_build_result.v1"
)
_MANGA_MASK_ASSISTANT_PACKAGE_BUILD_READINESS_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_package_build_readiness.v1"
)
_MANGA_MASK_ASSISTANT_CANDIDATE_ACCEPTANCE_RESULT_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_candidate_acceptance_result.v1"
)
_MANGA_MASK_ASSISTANT_SCORECARD_SNAPSHOT_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_scorecard_snapshot.v1"
)
_MANGA_MASK_ASSISTANT_PRODUCTION_SCORECARD_SNAPSHOT_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_production_scorecard_snapshot.v1"
)
_MANGA_INPAINT_PREVIEW_SUBMIT_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_inpaint_preview_submit_action_result.v1"
)
_MANGA_INPAINT_PREVIEW_HISTORY_RECORD_ACTION_RESULT_SCHEMA_VERSION = (
    "sam3.manga_mask_assistant_inpaint_preview_history_record_action_result.v1"
)
_MANGA_INPAINT_SUBMIT_ASYNC_BLOCKED_REASON = (
    "ai_select_manga_inpaint_submit_requires_async_executor"
)
_MANGA_MASK_ASSISTANT_SEND_TO_INPAINT_ACTION = "send_to_inpaint"
# Diff QA skips canvases beyond this pixel count (4096x4096-class); the QA
# runs synchronously on the main thread and stays advisory.
_GENERATION_DIFF_QA_MAX_CANVAS_PX = 16_777_216
# Aliased (not shared by reference) so tuning the diff QA budget never
# silently changes export behaviour. Both mean "4096x4096 readback ceiling".
_EXPORT_COMPOSITE_MAX_CANVAS_PX = _GENERATION_DIFF_QA_MAX_CANVAS_PX
_SAVE_MASK_ASSET_HISTORY_PAYLOAD_VERSION = "1.0"
_AI_EFFECT_PREVIEW_SUBMIT_HISTORY_PAYLOAD_VERSION = "1.0"
_AI_EFFECT_PREVIEW_SUBMIT_HISTORY_SOURCE = "sam3.ai_select.preview_generation_submit"
_MANGA_INPAINT_PREVIEW_HISTORY_PAYLOAD_VERSION = "1.0"
_MANGA_INPAINT_PREVIEW_HISTORY_SOURCE = "sam3.manga_inpaint.preview_generation_submit"
_GENERATION_DIFF_QA_HISTORY_PAYLOAD_VERSION = "1.0"
_GENERATION_DIFF_QA_HISTORY_SOURCE = "sam3.ai_select.generation_diff_qa_history"
_SAVE_MASK_ASSET_HISTORY_SERVICE_ID = "sam3_ai_select"
_RUN_ACTION_ID = "sam3_ai_select.run"
_AUTO_RUN_ACTION_ID = "sam3_ai_select.run.auto"
_REFINE_ACTION_ID = "sam3_ai_select.refine.current"
# SAM3 runs always target the active layer (2026-07-13 user decision).
_PINNED_SOURCE_SCOPE = "active_layer"


def _resolved_run_source_scope(settings: Any) -> str:
    """CT-23: the Source property chooses the host source; default stays active_layer."""
    raw = str(getattr(settings, "source_scope", "") or "").strip()
    return raw if raw in SAM3_AI_SELECT_SCOPE_VALUES else _PINNED_SOURCE_SCOPE


_CONTEXT_ACTION_RUN_STATUS = "sam3_ai_select.run.status"
_POINTER_BOX_MIN_SIZE = 1.0
_CONTEXT_ACTION_STATUS = "sam3_ai_select.candidate.status"
_CONTEXT_ACTION_SUMMARY = "sam3_ai_select.candidate.summary"
_CONTEXT_ACTION_TOGGLE_CANDIDATE_OVERLAY = "sam3_ai_select.overlay.toggle_candidate"
_CONTEXT_ACTION_PREVIOUS = "sam3_ai_select.candidate.previous"
_CONTEXT_ACTION_NEXT = "sam3_ai_select.candidate.next"
_CONTEXT_ACTION_REJECT = "sam3_ai_select.candidate.reject"
_CONTEXT_CANDIDATE_SELECT_ACTION_PREFIX = "sam3_ai_select.candidate.select."
_CONTEXT_CANDIDATE_REJECT_ACTION_PREFIX = "sam3_ai_select.candidate.reject."
_CONTEXT_CANDIDATE_REFINE_ACTION_PREFIX = "sam3_ai_select.candidate.refine."
_CONTEXT_CANDIDATE_APPLY_LAYER_MASK_ACTION_PREFIX = "sam3_ai_select.candidate.apply_layer_mask."
_CONTEXT_CANDIDATE_USE_INPAINT_MASK_ACTION_PREFIX = "sam3_ai_select.candidate.use_inpaint_mask."
_CONTEXT_CANDIDATE_USE_BACKGROUND_GENERATION_MASK_ACTION_PREFIX = (
    "sam3_ai_select.candidate.use_background_generation_mask."
)
_CONTEXT_CANDIDATE_PREVIEW_INPAINT_ACTION_PREFIX = "sam3_ai_select.candidate.preview_inpaint."
_CONTEXT_CANDIDATE_APPLY_CLIPPING_MASK_ACTION_PREFIX = (
    "sam3_ai_select.candidate.apply_clipping_mask."
)
_CONTEXT_CANDIDATE_CUT_TO_LAYER_ACTION_PREFIX = "sam3_ai_select.candidate.cut_to_new_layer."
_CONTEXT_CANDIDATE_EXTRACT_FOREGROUND_ACTION_PREFIX = "sam3_ai_select.candidate.extract_foreground."
_CONTEXT_CANDIDATE_EXTRACT_BACKGROUND_ACTION_PREFIX = "sam3_ai_select.candidate.extract_background."
_CONTEXT_CANDIDATE_SAVE_MASK_ASSET_ACTION_PREFIX = "sam3_ai_select.candidate.save_mask_asset."
_CONTEXT_ACTION_APPLY_CURRENT = "sam3_ai_select.apply.current"
_CONTEXT_ACTION_APPLY_CURRENT_AS_LAYER_MASK = "sam3_ai_select.apply.current_as_layer_mask"
_CONTEXT_ACTION_USE_CURRENT_AS_INPAINT_MASK = "sam3_ai_select.use.current_as_inpaint_mask"
_CONTEXT_ACTION_USE_CURRENT_AS_BACKGROUND_GENERATION_MASK = (
    "sam3_ai_select.use.current_as_background_generation_mask"
)
_CONTEXT_ACTION_PREVIEW_CURRENT_INPAINT = "sam3_ai_select.preview.current_inpaint"
_CONTEXT_ACTION_USE_SELECTION_AS_INPAINT_MASK = "sam3_ai_select.use.selection_as_inpaint_mask"
_CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT = "sam3_ai_select.preview.selection_inpaint"
_CONTEXT_ACTION_ADD_CURRENT_TO_LAYER_MASK = "sam3_ai_select.add.current_to_layer_mask"
_CONTEXT_ACTION_APPLY_CURRENT_AS_CLIPPING_MASK = "sam3_ai_select.apply.current_as_clipping_mask"
_CONTEXT_ACTION_APPLY_CURRENT_TO_NEW_LAYER = "sam3_ai_select.apply.current_to_new_layer"
_CONTEXT_ACTION_EXTRACT_CURRENT_FOREGROUND = "sam3_ai_select.extract.current_foreground"
_CONTEXT_ACTION_EXTRACT_CURRENT_BACKGROUND = "sam3_ai_select.extract.current_background"
_CONTEXT_ACTION_EXTRACT_TO_LAYER_IDS = (
    _CONTEXT_ACTION_EXTRACT_CURRENT_FOREGROUND,
    _CONTEXT_ACTION_EXTRACT_CURRENT_BACKGROUND,
)
_CONTEXT_ACTION_SAVE_CURRENT_MASK_ASSET = "sam3_ai_select.save.current_mask_asset"
_CONTEXT_ACTION_EXPORT_CURRENT_CANDIDATE = "sam3_ai_select.export.current_candidate"
_CONTEXT_ACTION_EXPORT_ALL_CANDIDATES = "sam3_ai_select.export.all_candidates"
_CONTEXT_ACTION_EXPORT_SPRITE_SHEET = "sam3_ai_select.export.sprite_sheet"
_CONTEXT_ACTION_EXPORT_SELECTION = "sam3_ai_select.export.selection"
_CONTEXT_EXPORT_ACTION_IDS = (
    _CONTEXT_ACTION_EXPORT_CURRENT_CANDIDATE,
    _CONTEXT_ACTION_EXPORT_ALL_CANDIDATES,
    _CONTEXT_ACTION_EXPORT_SPRITE_SHEET,
    _CONTEXT_ACTION_EXPORT_SELECTION,
)
# Explicit mapping (no fallback): a new export action that misses this table
# must fail loudly instead of silently reporting scope "candidate".
_ASSET_EXPORT_SCOPE_BY_ACTION_ID = {
    _CONTEXT_ACTION_EXPORT_CURRENT_CANDIDATE: "candidate",
    _CONTEXT_ACTION_EXPORT_ALL_CANDIDATES: "candidates",
    _CONTEXT_ACTION_EXPORT_SPRITE_SHEET: "sprite_sheet",
    _CONTEXT_ACTION_EXPORT_SELECTION: "selection",
}
_CONTEXT_ACTION_COMBINE_SELECTED = "sam3_ai_select.combine.selected"
_CONTEXT_ACTION_MASK_REFINE_SELECTED = "sam3_ai_select.mask_refine.selected"
_CONTEXT_ACTION_GROUP_SELECTED_TO_LAYER_GROUP = "sam3_ai_select.group.selected_to_layer_group"
_CONTEXT_OPERATION_ACTION_PREFIX = "sam3_ai_select.operation."
_CONTEXT_ACTION_APPLY_ADD = "sam3_ai_select.apply.add"
_CONTEXT_ACTION_APPLY_SUBTRACT = "sam3_ai_select.apply.subtract"
_CONTEXT_ACTION_APPLY_INTERSECT = "sam3_ai_select.apply.intersect"
_CONTEXT_APPLY_OPERATION_BY_ACTION_ID = {
    _CONTEXT_ACTION_APPLY_ADD: "add",
    _CONTEXT_ACTION_APPLY_SUBTRACT: "subtract",
    _CONTEXT_ACTION_APPLY_INTERSECT: "intersect",
}
_CANDIDATE_LIST_PANEL_DEFAULT_SIZE = (320, 420)
_MANGA_MASK_ASSISTANT_PANEL_DEFAULT_SIZE = (360, 460)
_CONTEXT_OPERATION_ACTION_IDS = tuple(
    f"{_CONTEXT_OPERATION_ACTION_PREFIX}{operation}"
    for operation in SAM3_AI_SELECT_OPERATION_VALUES
)
_CONTEXT_ACTION_IDS = (
    _CONTEXT_ACTION_RUN_STATUS,
    _CONTEXT_ACTION_STATUS,
    _CONTEXT_ACTION_SUMMARY,
    _CONTEXT_ACTION_PREVIOUS,
    _CONTEXT_ACTION_NEXT,
    _CONTEXT_ACTION_REJECT,
    _REFINE_ACTION_ID,
    _CONTEXT_ACTION_APPLY_CURRENT,
    _CONTEXT_ACTION_APPLY_CURRENT_AS_LAYER_MASK,
    _CONTEXT_ACTION_USE_CURRENT_AS_INPAINT_MASK,
    _CONTEXT_ACTION_PREVIEW_CURRENT_INPAINT,
    _CONTEXT_ACTION_USE_SELECTION_AS_INPAINT_MASK,
    _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT,
    _CONTEXT_ACTION_ADD_CURRENT_TO_LAYER_MASK,
    _CONTEXT_ACTION_TOGGLE_CANDIDATE_OVERLAY,
    _CONTEXT_ACTION_APPLY_CURRENT_AS_CLIPPING_MASK,
    _CONTEXT_ACTION_APPLY_CURRENT_TO_NEW_LAYER,
    *_CONTEXT_ACTION_EXTRACT_TO_LAYER_IDS,
    _CONTEXT_ACTION_SAVE_CURRENT_MASK_ASSET,
    *_CONTEXT_EXPORT_ACTION_IDS,
    _CONTEXT_ACTION_USE_CURRENT_AS_BACKGROUND_GENERATION_MASK,
    *_CONTEXT_OPERATION_ACTION_IDS,
    _CONTEXT_ACTION_APPLY_ADD,
    _CONTEXT_ACTION_APPLY_SUBTRACT,
    _CONTEXT_ACTION_APPLY_INTERSECT,
)
_SETTING_KEYS = frozenset(
    {
        "prompt_text",
        "mode",
        "scope",
        "source_scope",
        "apply_operation",
        "points",
        "boxes",
        "exemplar_asset_ids",
        "negative_text",
        "locale",
        "normalized_text",
        "max_candidates",
        "min_score",
        "mask_threshold",
        "split_disconnected_instances",
        "show_all_instances",
        "auto_mask_cleanup",
        "combine_mode",
        "preview_color_mode",
        "overlay_opacity",
        "include_rejected",
        "click_auto_run",
        "click_accumulate_auto_apply",
        "candidate_click_auto_apply",
        "apply_edge_feather_px",
        "apply_edge_smooth_px",
        "apply_edge_tightness_px",
        "export_padding_px",
        "export_source",
        "sheet_spacing_px",
        "sheet_power_of_two",
        "sheet_format",
        "metadata",
    }
)


class Sam3AISelectToolStateError(RuntimeError):
    """Raised when SAM3 AI Select tool state cannot satisfy an operation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Sam3AISelectToolPlugin:
    """Minimal ToolPlugin surface for SAM3 AI Select settings and registration."""

    def __init__(self) -> None:
        self.metadata = PluginMetadata(
            id=PLUGIN_ID,
            name=_ts("AI Select"),
            version="0.1.0",
            description=_ts("SAM3 AI selection tool shell"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )
        self.host_api: HostAPI | None = None
        self.settings = Sam3AiSelectToolSettings()
        self._registered = False
        self._last_run_result: Sam3AiSelectImageRunResult | None = None
        self._last_session: Sam3SelectionSession | None = None
        self._last_overlay_model: Sam3SelectionOverlayModel | None = None
        self._last_asset_store: Sam3AssetStore | None = None
        self._last_run_status: dict[str, Any] = _run_status_state_payload("idle")
        self._last_apply_plan: Sam3SelectionApplyPlan | None = None
        self._last_apply_result: Sam3SelectionApplyExecutionResult | None = None
        self._pointer_prompt_start: tuple[float, float] | None = None
        self._pointer_prompt_label: PointLabel = "positive"
        # Shift+click multi-selection on the canvas (Combine / Layer Group).
        self._multi_selected_candidate_ids: tuple[str, ...] = ()
        self._enabled = False
        self._overlay_installed = False
        self._candidate_overlay_visible = True
        self._candidate_list_panel: Any | None = None
        self._candidate_list_panel_registered = False
        self._last_manga_mask_assistant_package: Sam3ComicMaskPackage | None = None
        self._last_manga_inpaint_preparation: Sam3MangaInpaintPreparation | None = None
        self._last_manga_inpaint_preview_request: dict[str, object] | None = None
        self._last_manga_inpaint_preview_submit_result: dict[str, object] | None = None
        self._last_manga_inpaint_preview_history_metadata: dict[str, object] | None = None
        self._manga_mask_assistant_panel: Any | None = None
        self._manga_mask_assistant_panel_registered = False
        self._cached_worker_client: Any | None = None
        self._cached_worker_client_config: tuple | None = None
        self._cached_worker_asset_store: Sam3AssetStore | None = None
        self._preview_submit_in_flight = False
        self._auto_run_in_flight = False
        self._auto_run_pending = False
        self._pending_auto_apply_operation: str | None = None
        self._pointer_prompt_shift = False
        self._pointer_prompt_alt = False
        # Tied to the last selection bind (not the run session): kept across
        # runs and selection changes because the bound mask layer is a
        # snapshot; cleared on deactivate and overwritten by the next bind.
        self._last_selection_inpaint_mask_asset_id: str | None = None
        self._last_selection_inpaint_mask_layer_id: str | None = None
        self._last_preview_submit_status: str | None = None
        self._last_preview_submit_state: Sam3AsyncSubmitState | None = None
        self._last_preview_submit_blocked_reason_codes: list[str] = []
        self._last_preview_submit_diff_qa_ok: bool | None = None
        self._last_manga_inpaint_submit_status: str | None = None
        self._last_manga_inpaint_submit_blocked_reason_codes: list[str] = []
        self._last_manga_inpaint_submit_diff_qa_ok: bool | None = None
        self._provider_binding_lock = asyncio.Lock()

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id=TOOL_ID,
            display_name=_ts("AI Select"),
            cursor="crosshair",
            category="selection",
            order=_TOOL_ORDER,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "selection",
            tags=["selection", "sam3", "ai select"],
            group_id="selection.tools",
            group_label=_ts("Selection"),
            group_order=31,
            properties=[
                PropertySpec(
                    key="prompt_text",
                    display_name=_ts("Prompt"),
                    type=PropertyType.STRING,
                    default_value=self.settings.prompt_text or "",
                ),
                PropertySpec(
                    key="mode",
                    display_name=_ts("Mode"),
                    type=PropertyType.ENUM,
                    enum_items=list(SAM3_AI_SELECT_MODE_VALUES),
                    default_value=self.settings.mode,
                ),
                PropertySpec(
                    key="source_scope",
                    display_name=_ts("Source"),
                    type=PropertyType.ENUM,
                    enum_items=list(SAM3_AI_SELECT_SCOPE_VALUES),
                    default_value=self.settings.source_scope,
                ),
                PropertySpec(
                    key="apply_operation",
                    display_name=_ts("Operation"),
                    type=PropertyType.ENUM,
                    enum_items=list(SAM3_AI_SELECT_OPERATION_VALUES),
                    default_value=self.settings.apply_operation,
                ),
                PropertySpec(
                    key="max_candidates",
                    display_name=_ts("Max Candidates"),
                    type=PropertyType.INT,
                    min_value=1,
                    max_value=64,
                    default_value=self.settings.max_candidates,
                    step=1,
                ),
                PropertySpec(
                    key="min_score",
                    display_name=_ts("Min Candidate Score"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.min_score,
                    step=0.01,
                ),
                PropertySpec(
                    key="mask_threshold",
                    display_name=_ts("Detection Threshold"),
                    type=PropertyType.FLOAT,
                    min_value=0.05,
                    max_value=0.95,
                    default_value=self.settings.mask_threshold,
                    step=0.05,
                ),
                PropertySpec(
                    key="split_disconnected_instances",
                    display_name=_ts("Split Disconnected Instances"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.split_disconnected_instances,
                ),
                PropertySpec(
                    key="show_all_instances",
                    display_name=_ts("Show All Instances"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.show_all_instances,
                ),
                PropertySpec(
                    key="preview_color_mode",
                    display_name=_ts("Preview Color Mode"),
                    type=PropertyType.ENUM,
                    enum_items=list(SAM3_AI_SELECT_PREVIEW_COLOR_MODE_VALUES),
                    default_value=self.settings.preview_color_mode,
                ),
                PropertySpec(
                    key="overlay_opacity",
                    display_name=_ts("Overlay Opacity"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=1.0,
                    default_value=self.settings.overlay_opacity,
                    step=0.01,
                ),
                PropertySpec(
                    key="auto_mask_cleanup",
                    display_name=_ts("Auto Mask Cleanup"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.auto_mask_cleanup,
                ),
                PropertySpec(
                    key="combine_mode",
                    display_name=_ts("Combine Mode"),
                    type=PropertyType.ENUM,
                    enum_items=list(SAM3_AI_SELECT_COMBINE_MODE_VALUES),
                    default_value=self.settings.combine_mode,
                ),
                PropertySpec(
                    key="click_auto_run",
                    display_name=_ts("Run on Click"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.click_auto_run,
                ),
                PropertySpec(
                    key="click_accumulate_auto_apply",
                    display_name=_ts("Click Accumulate Apply"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.click_accumulate_auto_apply,
                ),
                PropertySpec(
                    key="candidate_click_auto_apply",
                    display_name=_ts("Apply on Candidate Click"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.candidate_click_auto_apply,
                ),
                PropertySpec(
                    key="apply_edge_feather_px",
                    display_name=_ts("Apply Edge Feather (px)"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=16,
                    default_value=self.settings.apply_edge_feather_px,
                    step=1,
                ),
                PropertySpec(
                    key="apply_edge_smooth_px",
                    display_name=_ts("Apply Edge Smooth (px)"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=16,
                    default_value=self.settings.apply_edge_smooth_px,
                    step=1,
                ),
                PropertySpec(
                    key="apply_edge_tightness_px",
                    display_name=_ts("Edge Tightness"),
                    type=PropertyType.INT,
                    min_value=-16,
                    max_value=16,
                    default_value=self.settings.apply_edge_tightness_px,
                    step=1,
                ),
                PropertySpec(
                    key="export_padding_px",
                    display_name=_ts("Export Padding (px)"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=64,
                    default_value=self.settings.export_padding_px,
                    step=1,
                ),
                PropertySpec(
                    key="export_source",
                    display_name=_ts("Export Source"),
                    type=PropertyType.ENUM,
                    enum_items=list(SAM3_AI_SELECT_EXPORT_SOURCE_VALUES),
                    default_value=self.settings.export_source,
                ),
                PropertySpec(
                    key="sheet_spacing_px",
                    display_name=_ts("Sheet Spacing (px)"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=16,
                    default_value=self.settings.sheet_spacing_px,
                    step=1,
                ),
                PropertySpec(
                    key="sheet_power_of_two",
                    display_name=_ts("Sheet Power of Two"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.sheet_power_of_two,
                ),
                PropertySpec(
                    key="sheet_format",
                    display_name=_ts("Sheet Format"),
                    type=PropertyType.ENUM,
                    enum_items=list(SAM3_AI_SELECT_SHEET_FORMAT_VALUES),
                    default_value=self.settings.sheet_format,
                ),
            ],
        )

    @property
    def last_run_result(self) -> Sam3AiSelectImageRunResult | None:
        return self._last_run_result

    @property
    def last_session(self) -> Sam3SelectionSession | None:
        return self._last_session

    @property
    def last_overlay_model(self) -> Sam3SelectionOverlayModel | None:
        return self._last_overlay_model

    @property
    def candidate_overlay_visible(self) -> bool:
        return self._candidate_overlay_visible

    @property
    def last_apply_plan(self) -> Sam3SelectionApplyPlan | None:
        return self._last_apply_plan

    @property
    def last_apply_result(self) -> Sam3SelectionApplyExecutionResult | None:
        return self._last_apply_result

    def activate(self, host: HostAPI) -> None:
        self.host_api = host
        if not (self._registered or self._tool_registered(host)):
            register_tool = getattr(host, "register_tool", None)
            if callable(register_tool):
                register_tool(self)
                self._registered = True
        self._register_candidate_list_panel(host)
        self._register_manga_mask_assistant_panel(host)

    def enable(self) -> None:
        self._enabled = True
        self._sync_overlay_hook()
        self._emit("tool.changed", {"tool": TOOL_ID})

    def disable(self) -> None:
        self._enabled = False
        self._clear_overlay_hook()
        self._clear_pointer_prompt_drag()

    def deactivate(self) -> None:
        self._clear_pointer_prompt_drag()
        self._auto_run_in_flight = False
        self._auto_run_pending = False
        self._pending_auto_apply_operation = None
        self._last_selection_inpaint_mask_asset_id = None
        self._last_selection_inpaint_mask_layer_id = None
        self._enabled = False
        self._clear_overlay_hook()
        self._release_cached_worker_client_sync()
        self._unregister_manga_mask_assistant_panel()
        self._unregister_candidate_list_panel()
        if self.host_api is not None and self._registered:
            unregister = getattr(self.host_api, "unregister_tool", None)
            if callable(unregister):
                unregister(TOOL_ID)
        self.host_api = None
        self._registered = False
        self.clear_run_state()

    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del ctrl, meta
        if self.settings.mode not in {"click", "box", "mixed"}:
            # Prompt (text) mode has no pointer prompt: a plain canvas click
            # selects the candidate under the cursor; Shift+click extends the
            # multi-selection like the other selection tools (2026-07-13).
            self._clear_pointer_prompt_drag()
            if not alt:
                point = _stroke_point_pos(pt)
                if shift:
                    self._toggle_multi_selected_candidate_at_point(point)
                else:
                    self._select_candidate_at_point(point)
            return None
        self._pointer_prompt_start = _stroke_point_pos(pt)
        self._pointer_prompt_label = "negative" if alt else "positive"
        # Click mode consumes Shift/Alt as accumulate gestures at pointer_up
        # (modifiers never reach on_pointer_up, so they are captured here).
        self._pointer_prompt_shift = shift
        self._pointer_prompt_alt = alt
        return None

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        del pts

    def on_pointer_up(self, pt: StrokePoint) -> None:
        start = self._pointer_prompt_start
        if start is None:
            return None
        label = self._pointer_prompt_label
        shift = self._pointer_prompt_shift
        alt = self._pointer_prompt_alt
        self._clear_pointer_prompt_drag()
        end = _stroke_point_pos(pt)
        mode = self.settings.mode

        if mode == "click":
            # Click-mode Alt means "subtract accumulate" (Photoshop style),
            # not a negative point; the point itself is always positive.
            # Every click overwrites the pending operation (None included) so
            # a stale gesture can never leak into a later plain click.
            self._pending_auto_apply_operation = self._click_accumulate_operation(shift, alt)
            self._replace_settings(
                {
                    "points": (PointPrompt(end[0], end[1], "positive"),),
                    "boxes": (),
                }
            )
            self._maybe_schedule_click_auto_run()
            return None

        box = _pointer_box_prompt(start, end, label)
        if mode == "box":
            if box is not None:
                self._replace_settings({"boxes": (box,), "points": ()})
            return None

        if mode == "mixed":
            if box is not None:
                self._replace_settings({"boxes": (*self.settings.boxes, box)})
            else:
                self._replace_settings(
                    {"points": (*self.settings.points, PointPrompt(end[0], end[1], label))}
                )
        return None

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        return None

    def _select_candidate_at_point(self, point: tuple[float, float]) -> bool:
        """Select the candidate under a canvas point; True when moved."""

        candidate_id = self._hit_test_candidate_id(point)
        if candidate_id is None:
            return False
        # A plain click replaces any Shift+click multi-selection.
        if self._multi_selected_candidate_ids:
            self._multi_selected_candidate_ids = ()
            self._sync_overlay_hook()
        session = self._last_session
        if session is None:
            return False
        moved = False
        if candidate_id != session.selected_candidate_id:
            try:
                self.select_candidate(candidate_id)
            except (Sam3AISelectToolStateError, Sam3SelectionSessionError):
                return False
            moved = True
        # Selection-tool semantics: clicking a candidate also creates the
        # regular canvas selection (2026-07-13 feedback), so users get
        # marching ants without an extra Apply press.
        if self.settings.candidate_click_auto_apply:
            self._execute_click_auto_apply(
                getattr(self.host_api, "canvas", None) if self.host_api is not None else None,
                self.settings.apply_operation,
            )
        return moved

    def _toggle_multi_selected_candidate_at_point(self, point: tuple[float, float]) -> bool:
        """Shift+click: toggle the candidate under the point in the multi-set."""

        candidate_id = self._hit_test_candidate_id(point)
        session = self._last_session
        if candidate_id is None or session is None:
            return False
        current = list(self._multi_selected_candidate_ids)
        if not current and session.selected_candidate_id is not None:
            # Extend FROM the current selection, like the selection tools.
            current = [session.selected_candidate_id]
        if candidate_id in current:
            current.remove(candidate_id)
        else:
            current.append(candidate_id)
        self._multi_selected_candidate_ids = tuple(current)
        self._sync_overlay_hook()
        self._refresh_candidate_list_panel()
        return True

    def _hit_test_candidate_id(self, point: tuple[float, float]) -> str | None:
        session = self._last_session
        overlay_model = self._last_overlay_model
        if session is None or overlay_model is None or not self._candidate_overlay_visible:
            return None
        from .candidate_hit_test import hit_test_candidate  # noqa: PLC0415

        return hit_test_candidate(
            overlay_model,
            self._last_asset_store,
            point[0],
            point[1],
        )

    def multi_selected_candidate_ids(self) -> tuple[str, ...]:
        """Current Shift+click multi-selection (Combine / Layer Group input)."""

        return self._multi_selected_candidate_ids

    def _prompt_mask_refine_options(self) -> dict[str, object] | None:
        """Open the morphology dialog; None when headless or cancelled."""

        widget = self._get_canvas_widget()
        if widget is None:
            return None
        try:
            from .mask_refine_dialog import show_mask_refine_dialog  # noqa: PLC0415

            payload = show_mask_refine_dialog(widget)
        except Exception:
            logger.debug("SAM3 mask refine dialog failed", exc_info=True)
            return None
        return dict(payload) if payload else None

    def on_key(self, key: str, pressed: bool) -> bool:
        return False

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        return False

    def build_commands(self) -> list[Command]:
        return []

    async def run_ai_select(
        self,
        provider: Sam3MaskProvider,
        *,
        image: object,
        request_id: str,
        snapshot_id: str | None = None,
        document_id: str | None = None,
        page_id: str | None = None,
        layer_id: str | None = None,
        dpi: int | None = None,
        available_layer_ids: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> Sam3AiSelectImageRunResult:
        self.clear_run_state()
        self._last_run_status = _run_status_state_payload("running")
        self._refresh_candidate_list_panel()
        self._emit("sam3_ai_select.run_started", {"tool": TOOL_ID, "requestId": request_id})
        try:
            result = await run_ai_select_from_qimage(
                provider,
                image=image,
                request_id=request_id,
                settings=self.settings,
                snapshot_id=snapshot_id,
                document_id=document_id,
                page_id=page_id,
                layer_id=layer_id,
                dpi=dpi,
                available_layer_ids=tuple(available_layer_ids),
                metadata=metadata,
            )
        except Exception as exc:
            self.clear_run_state()
            self._last_run_status = _run_status_state_payload(
                "failed",
                error_type=type(exc).__name__,
            )
            self._refresh_candidate_list_panel()
            self._emit(
                "sam3_ai_select.run_failed",
                {
                    "tool": TOOL_ID,
                    "requestId": request_id,
                    "errorType": type(exc).__name__,
                },
            )
            self._drain_pending_auto_run()
            raise

        self._last_run_result = result
        self._last_session = result.selection_result.session
        self._last_overlay_model = result.selection_result.overlay_model
        self._last_asset_store = provider.asset_store
        self._last_run_status = _run_status_state_payload(
            result.status,
            blocked_reason_codes=[
                reason.code for reason in result.selection_result.blocked_reasons
            ],
        )
        self._sync_overlay_hook()
        self._refresh_candidate_list_panel()
        event_name = (
            "sam3_ai_select.run_completed" if result.completed else "sam3_ai_select.run_blocked"
        )
        self._emit(event_name, _run_event_payload(result, request_id))
        self._drain_pending_auto_run()
        return result

    async def execute_run_action(
        self,
        provider: Sam3MaskProvider,
        *,
        image: object,
        request_id: str,
        snapshot_id: str | None = None,
        document_id: str | None = None,
        page_id: str | None = None,
        layer_id: str | None = None,
        dpi: int | None = None,
        available_layer_ids: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        readiness = self.get_run_readiness_snapshot()
        if not readiness["ready"]:
            self._last_run_status = _run_status_state_payload(
                "blocked",
                blocked_reason_codes=readiness["blockedReasonCodes"],
            )
            return _run_action_result_payload(
                status="blocked",
                blocked_reason_codes=readiness["blockedReasonCodes"],
            )
        try:
            result = await self.run_ai_select(
                provider,
                image=image,
                request_id=request_id,
                snapshot_id=snapshot_id,
                document_id=document_id,
                page_id=page_id,
                layer_id=layer_id,
                dpi=dpi,
                available_layer_ids=available_layer_ids,
                metadata=metadata,
            )
        except Exception as exc:
            return _run_action_result_payload(
                status="failed",
                error_type=type(exc).__name__,
            )
        return _run_action_result_payload(
            status=result.status,
            run_result=result,
            session=self._last_session,
            overlay_model=self._last_overlay_model,
            blocked_reason_codes=[
                reason.code for reason in result.selection_result.blocked_reasons
            ],
        )

    async def execute_host_source_run_action(
        self,
        provider: Sam3MaskProvider,
        *,
        host_api: HostAPI | None = None,
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        readiness = self.get_run_readiness_snapshot()
        if not readiness["ready"]:
            self._last_run_status = _run_status_state_payload(
                "blocked",
                blocked_reason_codes=readiness["blockedReasonCodes"],
            )
            return _run_action_result_payload(
                status="blocked",
                blocked_reason_codes=readiness["blockedReasonCodes"],
            )

        host = host_api if host_api is not None else self.host_api
        if host is None:
            self._last_run_status = _run_status_state_payload(
                "blocked",
                blocked_reason_codes=["ai_select_host_api_missing"],
            )
            return _run_action_result_payload(
                status="blocked",
                blocked_reason_codes=["ai_select_host_api_missing"],
            )

        # SAM3 の対象は常に選択 (アクティブ) レイヤーに限定する (2026-07-13)。
        # settings.scope は API 互換のため残るが、UI 経由の RUN はここで固定。
        source_scope = _resolved_run_source_scope(self.settings)
        if source_scope != self.settings.scope:
            self._replace_settings({"scope": source_scope})

        try:
            source = resolve_ai_select_source_image(host, scope=source_scope)
        except Sam3AiSelectSourceImageError as exc:
            self._last_run_status = _run_status_state_payload(
                "blocked",
                blocked_reason_codes=[exc.code],
            )
            self._refresh_candidate_list_panel()
            # Status-bar notice: e.g. RUN on a non-raster layer must tell the
            # user why nothing happened (2026-07-13 feedback).
            self._emit(
                "sam3_ai_select.run_source_blocked",
                {"tool": TOOL_ID, "blockedReasonCodes": [exc.code]},
            )
            return _run_action_result_payload(
                status="blocked",
                blocked_reason_codes=[exc.code],
            )
        except Exception as exc:
            self._last_run_status = _run_status_state_payload(
                "failed",
                error_type=type(exc).__name__,
            )
            return _run_action_result_payload(
                status="failed",
                error_type=type(exc).__name__,
            )

        return await self.execute_run_action(
            provider,
            request_id=request_id if request_id is not None else _default_run_request_id(),
            metadata=metadata,
            **source.to_run_kwargs(),
        )

    async def execute_bound_host_source_run_action(
        self,
        *,
        binding: Sam3ProviderBinding | None = None,
        host_api: HostAPI | None = None,
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        readiness = self.get_run_readiness_snapshot()
        if not readiness["ready"]:
            self._last_run_status = _run_status_state_payload(
                "blocked",
                blocked_reason_codes=readiness["blockedReasonCodes"],
            )
            return _run_action_result_payload(
                status="blocked",
                blocked_reason_codes=readiness["blockedReasonCodes"],
            )

        provider_binding = await self._resolve_provider_binding(binding)
        if not provider_binding.ready:
            blocked_reason_codes = list(provider_binding.blocked_reason_codes)
            if not blocked_reason_codes:
                blocked_reason_codes = ["sam3_provider_not_ready"]
            self._last_run_status = _run_status_state_payload(
                "blocked",
                blocked_reason_codes=blocked_reason_codes,
            )
            return _run_action_result_payload(
                status="blocked",
                blocked_reason_codes=blocked_reason_codes,
            )

        return await self.execute_host_source_run_action(
            provider_binding.provider,
            host_api=host_api,
            request_id=request_id,
            metadata=metadata,
        )

    def _click_accumulate_operation(self, shift: bool, alt: bool) -> str | None:
        """Map the captured click modifiers to an accumulate operation."""

        if not self.settings.click_accumulate_auto_apply:
            # OFF means modifiers are ignored entirely (plain-click parity).
            return None
        if shift:
            # Shift wins when both modifiers are held.
            return "add"
        if alt:
            return "subtract"
        return None

    def _execute_click_auto_apply(
        self,
        canvas: Sam3SelectionCanvasSubmitter | None,
        operation: str,
    ) -> None:
        """Apply the auto-run result with the click's accumulate operation.

        Emits auto_apply_applied / auto_apply_blocked on top of the regular
        apply events (never instead of them); a blocked apply keeps the run
        result and candidates so the user can still apply manually.
        """

        if canvas is None or not callable(getattr(canvas, "submit", None)):
            self._emit_auto_apply(operation, status="blocked", codes=["ai_select_canvas_missing"])
            return
        try:
            result = self.apply_current_candidate(canvas, operation=operation)
        except Sam3AISelectToolStateError as exc:
            self._emit_auto_apply(operation, status="blocked", codes=[exc.code])
            return
        if result.applied:
            self._emit_auto_apply(operation, status="applied", codes=[])
        else:
            self._emit_auto_apply(
                operation,
                status="blocked",
                codes=[reason.code for reason in result.blocked_reasons],
            )

    def _emit_auto_apply(self, operation: str, *, status: str, codes: list[str]) -> None:
        event_name = (
            "sam3_ai_select.auto_apply_applied"
            if status == "applied"
            else "sam3_ai_select.auto_apply_blocked"
        )
        self._emit(
            event_name,
            {
                "tool": TOOL_ID,
                "operation": operation,
                "status": status,
                "blockedReasonCodes": list(codes),
            },
        )

    def _maybe_schedule_click_auto_run(self) -> None:
        """Schedule an automatic RUN after a click-mode point is placed.

        Uses the host tool manager's async action scheduler (the same
        machinery the panels use for sentinel escalation); without it
        (headless probes) the click keeps the manual-RUN behaviour.
        """

        if not self.settings.click_auto_run:
            return
        run_status = self._last_run_status or {}
        if self._auto_run_in_flight or run_status.get("status") == "running":
            # Trailing-edge debounce (covers a manual RUN in flight too):
            # keep exactly one pending re-run; run_ai_select drains it on
            # completion either way.
            self._auto_run_pending = True
            return
        scheduler = _candidate_list_action_scheduler(self.host_api)
        if scheduler is None:
            return
        if not scheduler(_AUTO_RUN_ACTION_ID, None):
            logger.debug("SAM3 AI Select click auto-run scheduling failed")

    async def _execute_bound_auto_run_action(
        self,
        *,
        binding: Sam3ProviderBinding | None = None,
        host_api: HostAPI | None = None,
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        canvas: Sam3SelectionCanvasSubmitter | None = None,
    ) -> dict[str, Any]:
        if self._auto_run_in_flight:
            self._auto_run_pending = True
            return _run_action_result_payload(
                status="blocked",
                blocked_reason_codes=["ai_select_auto_run_in_flight"],
            )
        # Entry-capture: this run owns the operation of the click that
        # scheduled it. A click arriving while this run is in flight
        # overwrites the pending slot and its drained run captures it —
        # never this one (reading it after the run would misattribute).
        captured_operation = self._pending_auto_apply_operation
        self._pending_auto_apply_operation = None
        self._auto_run_in_flight = True
        try:
            readiness = self.get_run_readiness_snapshot()
            if not readiness["ready"]:
                # Auto-run users never press RUN, so a silent return would
                # read as "the click did nothing" — surface the block.
                blocked_codes = list(readiness["blockedReasonCodes"])
                self._last_run_status = _run_status_state_payload(
                    "blocked",
                    blocked_reason_codes=blocked_codes,
                )
                self._refresh_candidate_list_panel()
                self._emit(
                    "sam3_ai_select.auto_run_blocked",
                    {
                        "tool": TOOL_ID,
                        "source": "click_auto_run",
                        "blockedReasonCodes": blocked_codes,
                    },
                )
                return _run_action_result_payload(
                    status="blocked",
                    blocked_reason_codes=blocked_codes,
                )
            result = await self.execute_bound_host_source_run_action(
                binding=binding,
                host_api=host_api,
                request_id=request_id,
                metadata=metadata,
            )
            if (
                captured_operation is not None
                and _clean_optional_text(result.get("status")) == "completed"
            ):
                self._execute_click_auto_apply(canvas, captured_operation)
            return result
        finally:
            self._auto_run_in_flight = False
            self._drain_pending_auto_run()

    def _drain_pending_auto_run(self) -> None:
        if not self._auto_run_pending:
            return
        self._auto_run_pending = False
        # Re-check the gates at drain time: a mode switch or setting change
        # while the run was in flight discards the pending click.
        if self.settings.mode != "click" or not self.settings.click_auto_run:
            return
        if (self._last_run_status or {}).get("status") == "running":
            # Another run took the slot meanwhile; it will drain again.
            self._auto_run_pending = True
            return
        scheduler = _candidate_list_action_scheduler(self.host_api)
        if scheduler is None:
            return
        if not scheduler(_AUTO_RUN_ACTION_ID, None):
            logger.debug("SAM3 AI Select pending auto-run scheduling failed")

    async def _resolve_provider_binding(
        self,
        binding: Sam3ProviderBinding | None = None,
    ) -> Sam3ProviderBinding:
        if binding is not None:
            return binding
        async with self._provider_binding_lock:
            host_settings = self._host_settings_service()
            if host_settings is None:
                await self._close_cached_worker_client()
                return await resolve_sam3_provider_binding()
            cache_config = self._worker_client_cache_config(host_settings)
            if cache_config is None or cache_config != self._cached_worker_client_config:
                await self._close_cached_worker_client()
            if cache_config is not None and self._cached_worker_client is not None:
                return await resolve_sam3_provider_binding(
                    worker_client=self._cached_worker_client,
                    asset_store=self._cached_worker_asset_store,
                    runtime=cache_config[0],
                )
            asset_store = Sam3AssetStore()
            worker_client, runtime = create_configured_sam3_worker_client(
                host_settings,
                asset_store=asset_store,
            )
            resolved = await resolve_sam3_provider_binding(
                worker_client=worker_client,
                asset_store=asset_store,
                runtime=runtime,
            )
            if cache_config is not None and runtime == cache_config[0] and resolved.ready:
                self._cached_worker_client = worker_client
                self._cached_worker_client_config = cache_config
                self._cached_worker_asset_store = asset_store
            return resolved

    def _worker_client_cache_config(self, host_settings: object) -> tuple | None:
        """Cache key for reusable worker clients: (runtime mode, config).

        The SAM 3.1 bridge is cached too — its in-memory object registry
        carries refine continuity between segment and refine calls.
        """
        mode = ai_select_runtime_mode(host_settings)
        if mode == AI_SELECT_RUNTIME_MODE_SAM31:
            from loomstroke.sam31.runtime_config import (  # noqa: PLC0415
                build_sam31_runtime_config,
            )

            return (mode, build_sam31_runtime_config(host_settings))
        return None

    async def _close_cached_worker_client(self) -> None:
        client = self._cached_worker_client
        self._cached_worker_client = None
        self._cached_worker_client_config = None
        self._cached_worker_asset_store = None
        if client is None:
            return
        try:
            await client.close()
        except Exception:
            logger.warning("SAM3 worker client close failed", exc_info=True)

    def _release_cached_worker_client_sync(self) -> None:
        client = self._cached_worker_client
        self._cached_worker_client = None
        self._cached_worker_client_config = None
        self._cached_worker_asset_store = None
        if client is None:
            return
        terminate = getattr(client, "terminate_sync", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:
                logger.warning("SAM3 worker terminate failed", exc_info=True)

    def _host_settings_service(self) -> object | None:
        host_api = self.host_api
        if host_api is None:
            return None
        settings = getattr(host_api, "settings", None)
        return settings if settings is not None else None

    def clear_run_state(self) -> None:
        self._last_run_result = None
        self._last_session = None
        self._last_overlay_model = None
        self._last_asset_store = None
        self._last_run_status = _run_status_state_payload("idle")
        self._last_apply_plan = None
        self._last_apply_result = None
        self._candidate_overlay_visible = True
        self._multi_selected_candidate_ids = ()
        self._sync_overlay_hook()
        self._refresh_candidate_list_panel()

    def select_candidate(self, candidate_id: str) -> Sam3SelectionSession:
        return self._replace_session(
            select_session_candidate(self._require_session(), candidate_id),
            action="select",
        )

    def select_next_candidate(self) -> Sam3SelectionSession:
        return self._replace_session(
            select_next_session_candidate(self._require_session()),
            action="next",
        )

    def select_previous_candidate(self) -> Sam3SelectionSession:
        return self._replace_session(
            select_previous_session_candidate(self._require_session()),
            action="previous",
        )

    def reject_candidate(self, candidate_id: str | None = None) -> Sam3SelectionSession:
        return self._replace_session(
            reject_session_candidate(self._require_session(), candidate_id),
            action="reject",
        )

    def _apply_edge_refine_options(self) -> MaskRefineOptions | None:
        """Edge finish for the interactive candidate apply path only.

        protect_text, manga row apply and the MCP canvas agent build their
        plans elsewhere and intentionally keep hard mask edges.
        """

        feather = int(self.settings.apply_edge_feather_px)
        smooth = int(self.settings.apply_edge_smooth_px)
        tightness = int(self.settings.apply_edge_tightness_px)
        contract = max(0, tightness)  # 正 = 収縮 / タイト
        expand = max(0, -tightness)  # 負 = 膨張 / 緩く
        if feather <= 0 and smooth <= 0 and contract <= 0 and expand <= 0:
            return None
        return MaskRefineOptions(
            feather_px=float(feather),
            smooth_radius_px=float(smooth),
            contract_px=contract,
            expand_px=expand,
        )

    def set_apply_operation(self, operation: str) -> Sam3SelectionSession:
        session = self._require_session()
        self._replace_settings({"apply_operation": operation})
        return self._replace_session(
            replace(session, operation=self.settings.apply_operation),
            action="operation",
        )

    def apply_current_candidate(
        self,
        canvas: Sam3SelectionCanvasSubmitter,
        *,
        source: str | None = "sam3.selection.apply",
        merge_key: str | None = None,
        asset_store: Sam3AssetStore | None = None,
        operation: str | None = None,
    ) -> Sam3SelectionApplyExecutionResult:
        session = self._require_session()
        if operation is not None:
            # One-shot operation override: validated by the session's
            # __post_init__ (invalid values raise Sam3SelectionSessionError)
            # and never written back to the tool settings.
            session = replace(session, operation=operation)
        store = asset_store if asset_store is not None else self._last_asset_store
        if store is None:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            )
        result = apply_selection_session(
            session,
            store,
            canvas,
            source=source,
            merge_key=merge_key,
            refine_options=self._apply_edge_refine_options(),
        )
        self._last_apply_plan = result.plan
        self._last_apply_result = result
        event_name = (
            "sam3_ai_select.apply_applied" if result.applied else "sam3_ai_select.apply_blocked"
        )
        self._emit(event_name, _apply_event_payload(result))
        return result

    async def refine_current_candidate(
        self,
        provider: Sam3MaskProvider,
        *,
        request_id: str | None = None,
        candidate_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Sam3SelectionRefineResult:
        session = self._require_session()
        clean_request_id = request_id if request_id is not None else _default_refine_request_id()
        # Target diagnosability: candidate ids are internal safe ids, so this
        # is the one log line that proves which candidate a refine ran on.
        logger.info(
            "SAM3 AI Select refine target: candidate=%s (explicit=%s, selected=%s)",
            candidate_id or session.selected_candidate_id,
            candidate_id,
            session.selected_candidate_id,
        )
        overlay_options = (
            self._last_run_result.run_config.overlay_options
            if self._last_run_result is not None
            else self.settings.to_overlay_options()
        )
        # Status-bar indicator hook: always paired with a terminal
        # refine_completed / refine_blocked / refine_failed event.
        self._emit(
            "sam3_ai_select.refine_started",
            {
                "tool": TOOL_ID,
                "requestId": clean_request_id,
                "candidateId": candidate_id or session.selected_candidate_id,
            },
        )
        try:
            result = await refine_selection_candidate(
                provider,
                request_id=clean_request_id,
                session=session,
                candidate_id=candidate_id,
                overlay_options=overlay_options,
                options={
                    **SAM3_SELECTION_REFINE_DEFAULT_OPTIONS,
                    "autoCleanup": self.settings.auto_mask_cleanup,
                },
                metadata=metadata,
            )
        except Exception:
            self._emit(
                "sam3_ai_select.refine_failed",
                {"tool": TOOL_ID, "requestId": clean_request_id, "status": "failed"},
            )
            raise
        if result.completed:
            self._apply_completed_refine_result(provider, result)
            self._emit("sam3_ai_select.refine_completed", _refine_event_payload(result))
            return result

        blocked_reason_codes = [reason.code for reason in result.blocked_reasons]
        self._last_run_status = _run_status_state_payload(
            "blocked",
            blocked_reason_codes=blocked_reason_codes,
        )
        self._emit("sam3_ai_select.refine_blocked", _refine_event_payload(result))
        return result

    async def execute_refine_action(
        self,
        provider: Sam3MaskProvider,
        *,
        action_id: str = _REFINE_ACTION_ID,
        request_id: str | None = None,
        candidate_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            result = await self.refine_current_candidate(
                provider,
                request_id=request_id,
                candidate_id=candidate_id,
                metadata=metadata,
            )
        except Sam3AISelectToolStateError as exc:
            return _refine_action_result_payload(
                action_id=action_id,
                status="blocked",
                blocked_reason_codes=[exc.code],
            )
        except Exception as exc:
            return _refine_action_result_payload(
                action_id=action_id,
                status="failed",
                error_type=type(exc).__name__,
            )

        return _refine_action_result_payload(
            action_id=action_id,
            status=result.status,
            refine_result=result,
            session=self._last_session if result.completed else None,
            overlay_model=self._last_overlay_model if result.completed else None,
            blocked_reason_codes=[reason.code for reason in result.blocked_reasons],
        )

    def build_current_apply_plan(self) -> Sam3SelectionApplyPlan:
        plan = build_session_apply_plan(self._require_session())
        self._last_apply_plan = plan
        event_name = (
            "sam3_ai_select.apply_plan_ready" if plan.ready else "sam3_ai_select.apply_plan_blocked"
        )
        self._emit(event_name, _apply_plan_event_payload(plan))
        return plan

    def get_state_snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": _TOOL_STATE_SCHEMA_VERSION,
            "tool": TOOL_ID,
            "registered": self._registered,
            "hasRunResult": self._last_run_result is not None,
            "run": (
                _run_state_payload(self._last_run_result)
                if self._last_run_result is not None
                else None
            ),
            "session": (
                _session_state_payload(self._last_session)
                if self._last_session is not None
                else None
            ),
            "overlay": (
                _overlay_state_payload(self._last_overlay_model)
                if self._last_overlay_model is not None
                else None
            ),
            "applyPlan": (
                _apply_plan_event_payload(self._last_apply_plan)
                if self._last_apply_plan is not None
                else None
            ),
            "lastApply": (
                _apply_event_payload(self._last_apply_result)
                if self._last_apply_result is not None
                else None
            ),
        }

    def get_candidate_list_snapshot(self) -> dict[str, Any]:
        snapshot = _candidate_list_snapshot_payload(self._last_overlay_model)
        snapshot["previewSubmitStatus"] = self._last_preview_submit_status
        snapshot["previewSubmitBlockedReasonCodes"] = list(
            self._last_preview_submit_blocked_reason_codes
        )
        snapshot["previewSubmitDiffQaOk"] = self._last_preview_submit_diff_qa_ok
        state = self._last_preview_submit_state
        snapshot["previewSubmitState"] = state.to_dict() if state is not None else None
        return snapshot

    def get_candidate_list_view_model_snapshot(self) -> dict[str, Any]:
        return build_candidate_list_view_model(
            self.get_candidate_list_snapshot(),
            self.get_context_actions_snapshot(),
        )

    def set_manga_mask_assistant_package(
        self,
        package: Sam3ComicMaskPackage | None,
    ) -> None:
        self._last_manga_mask_assistant_package = package
        self._last_manga_inpaint_preparation = None
        self._last_manga_inpaint_preview_request = None
        self._last_manga_inpaint_preview_submit_result = None
        self._last_manga_inpaint_preview_history_metadata = None
        self._refresh_manga_mask_assistant_panel()

    def set_manga_mask_assistant_candidate_acceptance(
        self,
        candidate_id: object,
        accepted: object,
        *,
        scope: object | None = None,
        panel_id: object | None = None,
    ) -> dict[str, Any]:
        normalized_candidate_id = str(candidate_id or "").strip()
        normalized_scope = str(scope or "").strip() or None
        payload_scope = normalized_scope if normalized_scope in {"panel", "global"} else None
        normalized_panel_id = str(panel_id or "").strip() or None
        normalized_accepted = bool(accepted)
        package = self._last_manga_mask_assistant_package
        if package is None:
            return _manga_mask_assistant_candidate_acceptance_result_payload(
                candidate_id=normalized_candidate_id or None,
                accepted=normalized_accepted,
                scope=payload_scope,
                panel_id=normalized_panel_id,
                blocked_reason_codes=("manga_mask_assistant_package_missing",),
            )
        if not normalized_candidate_id:
            return _manga_mask_assistant_candidate_acceptance_result_payload(
                accepted=normalized_accepted,
                scope=payload_scope,
                panel_id=normalized_panel_id,
                blocked_reason_codes=("manga_mask_assistant_candidate_id_missing",),
            )
        if normalized_scope not in {None, "panel", "global"}:
            return _manga_mask_assistant_candidate_acceptance_result_payload(
                candidate_id=normalized_candidate_id,
                accepted=normalized_accepted,
                scope=None,
                panel_id=normalized_panel_id,
                blocked_reason_codes=("manga_mask_assistant_candidate_scope_invalid",),
            )

        try:
            updated_package = with_comic_mask_candidate_acceptance(
                package,
                normalized_candidate_id,
                normalized_accepted,
                scope=normalized_scope,
                panel_id=normalized_panel_id,
            )
        except Sam3ComicMaskPackageError:
            return _manga_mask_assistant_candidate_acceptance_result_payload(
                candidate_id=normalized_candidate_id,
                accepted=normalized_accepted,
                scope=normalized_scope,
                panel_id=normalized_panel_id,
                blocked_reason_codes=("manga_mask_assistant_candidate_acceptance_target_missing",),
            )
        except Exception as exc:  # noqa: BLE001 - keep acceptance result scrubbed.
            logger.debug(
                "SAM3 Manga Mask Assistant candidate acceptance update failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_candidate_acceptance_result_payload(
                status="failed",
                candidate_id=normalized_candidate_id,
                accepted=normalized_accepted,
                scope=normalized_scope,
                panel_id=normalized_panel_id,
                error_type=type(exc).__name__,
            )

        self.set_manga_mask_assistant_package(updated_package)
        package_summary = _manga_mask_assistant_package_summary(
            updated_package,
            candidate_set_id=_manga_mask_assistant_candidate_set_id(self._last_session),
        )
        payload = _manga_mask_assistant_candidate_acceptance_result_payload(
            status="updated",
            handled=True,
            candidate_id=normalized_candidate_id,
            accepted=normalized_accepted,
            scope=normalized_scope,
            panel_id=normalized_panel_id,
            package=package_summary,
        )
        self._emit(
            "sam3_ai_select.manga_mask_assistant_candidate_acceptance_updated",
            dict(payload),
        )
        return payload

    def get_manga_mask_assistant_package_build_readiness_snapshot(
        self,
        category: object | None = None,
        *,
        panel_bboxes: Mapping[str, object] | None = None,
        category_by_candidate_id: Mapping[str, object] | None = None,
        accepted_candidate_ids: Iterable[object] = (),
        warnings: object = (),
    ) -> dict[str, Any]:
        session = self._last_session
        if session is None:
            return _manga_mask_assistant_package_build_readiness_payload(
                blocked_reason_codes=("manga_mask_assistant_session_missing",),
            )

        candidate_set = getattr(session, "candidate_set", None)
        if candidate_set is None:
            return _manga_mask_assistant_package_build_readiness_payload(
                has_session=True,
                blocked_reason_codes=("manga_mask_assistant_candidate_set_missing",),
            )
        if category is None or (isinstance(category, str) and not category.strip()):
            return _manga_mask_assistant_package_build_readiness_payload(
                has_session=True,
                candidate_set_id=candidate_set.set_id,
                candidate_count=len(candidate_set.candidates),
                blocked_reason_codes=("manga_mask_assistant_category_required",),
            )

        try:
            package = build_comic_mask_package_from_candidate_set(
                candidate_set,
                category,
                panel_bboxes=panel_bboxes,
                category_by_candidate_id=category_by_candidate_id,
                accepted_candidate_ids=accepted_candidate_ids,
                warnings=warnings,
            )
        except Sam3ComicMaskPackageError:
            return _manga_mask_assistant_package_build_readiness_payload(
                has_session=True,
                candidate_set_id=candidate_set.set_id,
                candidate_count=len(candidate_set.candidates),
                blocked_reason_codes=("manga_mask_assistant_package_build_invalid",),
            )
        except Exception as exc:  # noqa: BLE001 - scrubbed readiness failure.
            logger.debug(
                "SAM3 Manga Mask Assistant package readiness failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_package_build_readiness_payload(
                has_session=True,
                candidate_set_id=candidate_set.set_id,
                candidate_count=len(candidate_set.candidates),
                error_type=type(exc).__name__,
            )

        return _manga_mask_assistant_package_build_readiness_payload(
            ready=True,
            has_session=True,
            candidate_set_id=candidate_set.set_id,
            candidate_count=len(candidate_set.candidates),
            package=_manga_mask_assistant_package_summary(
                package,
                candidate_set_id=candidate_set.set_id,
            ),
        )

    def build_manga_mask_assistant_package_from_last_session(
        self,
        category: object,
        *,
        panel_bboxes: Mapping[str, object] | None = None,
        category_by_candidate_id: Mapping[str, object] | None = None,
        accepted_candidate_ids: Iterable[object] = (),
        package_id: str | None = None,
        source_document_id: str | None = None,
        source_page_id: str | None = None,
        warnings: object = (),
    ) -> dict[str, Any]:
        session = self._last_session
        if session is None:
            return _manga_mask_assistant_package_build_result_payload(
                blocked_reason_codes=("manga_mask_assistant_session_missing",),
            )

        candidate_set = getattr(session, "candidate_set", None)
        if candidate_set is None:
            return _manga_mask_assistant_package_build_result_payload(
                blocked_reason_codes=("manga_mask_assistant_candidate_set_missing",),
            )

        try:
            package = build_comic_mask_package_from_candidate_set(
                candidate_set,
                category,
                panel_bboxes=panel_bboxes,
                category_by_candidate_id=category_by_candidate_id,
                accepted_candidate_ids=accepted_candidate_ids,
                package_id=package_id,
                source_document_id=source_document_id,
                source_page_id=source_page_id,
                warnings=warnings,
            )
        except Sam3ComicMaskPackageError:
            return _manga_mask_assistant_package_build_result_payload(
                blocked_reason_codes=("manga_mask_assistant_package_build_invalid",),
            )
        except Exception as exc:  # noqa: BLE001 - scrubbed package bridge failure.
            logger.debug(
                "SAM3 Manga Mask Assistant package bridge failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_package_build_result_payload(
                status="failed",
                error_type=type(exc).__name__,
            )

        self.set_manga_mask_assistant_package(package)
        package_summary = _manga_mask_assistant_package_summary(
            package,
            candidate_set_id=candidate_set.set_id,
        )
        payload = _manga_mask_assistant_package_build_result_payload(
            status="built",
            handled=True,
            package=package_summary,
        )
        self._emit(
            "sam3_ai_select.manga_mask_assistant_package_built",
            {
                "schemaVersion": _MANGA_MASK_ASSISTANT_PACKAGE_BUILD_RESULT_SCHEMA_VERSION,
                "tool": TOOL_ID,
                "status": "built",
                "handled": True,
                "package": dict(package_summary),
                "blockedReasonCodes": [],
            },
        )
        return payload

    def get_manga_mask_assistant_table_model_snapshot(
        self,
        preset: str = "all",
    ) -> dict[str, Any]:
        # Submit status fields are attached in this single spot so every
        # return path (invalid preset / missing package / errors) carries
        # them for the panel status line.
        snapshot = self._manga_mask_assistant_table_model_snapshot_base(preset)
        snapshot["mangaInpaintSubmitStatus"] = self._last_manga_inpaint_submit_status
        snapshot["mangaInpaintSubmitBlockedReasonCodes"] = list(
            self._last_manga_inpaint_submit_blocked_reason_codes
        )
        snapshot["mangaInpaintSubmitDiffQaOk"] = self._last_manga_inpaint_submit_diff_qa_ok
        return snapshot

    def _manga_mask_assistant_table_model_snapshot_base(
        self,
        preset: str = "all",
    ) -> dict[str, Any]:
        active_preset = _manga_mask_assistant_active_preset(preset)
        if active_preset is None:
            return _manga_mask_assistant_empty_table_model(
                "all",
                warning_codes=("manga_mask_assistant_preset_invalid",),
            )

        package = self._last_manga_mask_assistant_package
        if package is None:
            return _manga_mask_assistant_empty_table_model(
                active_preset,
                warning_codes=("manga_mask_assistant_package_missing",),
            )

        try:
            view_model = build_manga_mask_assistant_view_model(package)
            filter_result = filter_manga_mask_assistant_view_model(view_model, active_preset)
            return dict(build_manga_mask_assistant_table_model(filter_result).to_dict())
        except Exception as exc:  # noqa: BLE001 - snapshots must stay JSON-safe.
            logger.debug(
                "SAM3 Manga Mask Assistant snapshot failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_empty_table_model(
                active_preset,
                warning_codes=_manga_mask_assistant_warning_codes(exc),
            )

    def get_manga_mask_assistant_scorecard_snapshot(
        self,
        *,
        canvas_width: object | None = None,
        canvas_height: object | None = None,
        min_confidence: object = 0.35,
        min_area_ratio: object = 0.001,
        max_area_ratio: object = 0.95,
    ) -> dict[str, Any]:
        package = self._last_manga_mask_assistant_package
        if package is None:
            return _manga_mask_assistant_scorecard_snapshot_payload(
                blocked_reason_codes=("manga_mask_assistant_package_missing",),
            )

        try:
            scorecard = score_comic_mask_package_candidates(
                package,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                min_confidence=min_confidence,
                min_area_ratio=min_area_ratio,
                max_area_ratio=max_area_ratio,
            )
        except Sam3MangaMaskScorecardError:
            return _manga_mask_assistant_scorecard_snapshot_payload(
                package_id=package.package_id,
                blocked_reason_codes=("manga_mask_assistant_scorecard_invalid",),
            )
        except Exception as exc:  # noqa: BLE001 - snapshots must stay JSON-safe.
            logger.debug(
                "SAM3 Manga Mask Assistant scorecard snapshot failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_scorecard_snapshot_payload(
                package_id=package.package_id,
                error_type=type(exc).__name__,
            )

        return _manga_mask_assistant_scorecard_snapshot_payload(
            ready=True,
            ok=scorecard.ok,
            package_id=scorecard.package_id,
            candidate_count=scorecard.candidate_count,
            accepted_candidate_count=scorecard.accepted_candidate_count,
            ready_candidate_count=scorecard.ready_candidate_count,
            scorecard=scorecard.to_dict(),
        )

    def get_manga_mask_assistant_production_scorecard_snapshot(
        self,
        *,
        canvas_width: object | None = None,
        canvas_height: object | None = None,
        min_confidence: object = 0.35,
        min_area_ratio: object = 0.001,
        max_area_ratio: object = 0.95,
        quality_profile: object = "sam3_manga_mask_quality",
        job_id: object | None = None,
        artifact_id: object | None = None,
    ) -> dict[str, Any]:
        package = self._last_manga_mask_assistant_package
        if package is None:
            return _manga_mask_assistant_production_scorecard_snapshot_payload(
                blocked_reason_codes=("manga_mask_assistant_package_missing",),
            )

        try:
            scorecard = score_comic_mask_package_candidates(
                package,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                min_confidence=min_confidence,
                min_area_ratio=min_area_ratio,
                max_area_ratio=max_area_ratio,
            )
        except Sam3MangaMaskScorecardError:
            return _manga_mask_assistant_production_scorecard_snapshot_payload(
                package_id=package.package_id,
                blocked_reason_codes=("manga_mask_assistant_scorecard_invalid",),
            )
        except Exception as exc:  # noqa: BLE001 - snapshots must stay JSON-safe.
            logger.debug(
                "SAM3 Manga Mask Assistant production scorecard scoring failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_production_scorecard_snapshot_payload(
                package_id=package.package_id,
                error_type=type(exc).__name__,
            )

        try:
            production_projection = build_manga_mask_scorecard_production_projection(
                scorecard,
                job_id=job_id,
                artifact_id=artifact_id,
                quality_profile=quality_profile,
            )
        except Sam3MangaMaskScorecardProjectionError:
            return _manga_mask_assistant_production_scorecard_snapshot_payload(
                package_id=scorecard.package_id,
                candidate_count=scorecard.candidate_count,
                accepted_candidate_count=scorecard.accepted_candidate_count,
                ready_candidate_count=scorecard.ready_candidate_count,
                blocked_reason_codes=("manga_mask_assistant_production_scorecard_invalid",),
            )
        except Exception as exc:  # noqa: BLE001 - snapshots must stay JSON-safe.
            logger.debug(
                "SAM3 Manga Mask Assistant production scorecard projection failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_production_scorecard_snapshot_payload(
                package_id=scorecard.package_id,
                candidate_count=scorecard.candidate_count,
                accepted_candidate_count=scorecard.accepted_candidate_count,
                ready_candidate_count=scorecard.ready_candidate_count,
                error_type=type(exc).__name__,
            )

        return _manga_mask_assistant_production_scorecard_snapshot_payload(
            ready=True,
            ok=production_projection.ok,
            package_id=production_projection.package_id,
            candidate_count=production_projection.candidate_count,
            accepted_candidate_count=production_projection.accepted_candidate_count,
            ready_candidate_count=production_projection.ready_candidate_count,
            selected_candidate_id=production_projection.selected_candidate_id,
            production_projection=production_projection.to_dict(),
        )

    def execute_manga_mask_assistant_action(
        self,
        action_id: str,
        *,
        export_output_dir: str | Path | None = None,
        export_allowed_roots: Sequence[str | Path] = (),
    ) -> dict[str, Any]:
        normalized_action_id = str(action_id or "").strip()
        if not normalized_action_id:
            payload = _manga_mask_assistant_action_result_payload(
                normalized_action_id,
                blocked_reason_codes=("manga_mask_assistant_action_id_missing",),
            )
        else:
            target = parse_manga_mask_assistant_action_id(normalized_action_id)
            if target is None:
                payload = _manga_mask_assistant_action_result_payload(
                    normalized_action_id,
                    blocked_reason_codes=("manga_mask_assistant_action_id_invalid",),
                )
            elif self._last_manga_mask_assistant_package is None:
                payload = _manga_mask_assistant_action_result_payload(
                    normalized_action_id,
                    action=target.action,
                    row_id=target.row_id,
                    blocked_reason_codes=("manga_mask_assistant_package_missing",),
                )
            else:
                request = _manga_mask_assistant_resolve_action_request(
                    normalized_action_id,
                    self._last_manga_mask_assistant_package,
                    target,
                )
                if isinstance(request, Mapping):
                    payload = dict(request)
                else:
                    payload = self._execute_manga_mask_assistant_resolved_action(
                        request,
                        export_output_dir=export_output_dir,
                        export_allowed_roots=export_allowed_roots,
                    )
        return payload

    def _execute_manga_mask_assistant_resolved_action(
        self,
        request: Any,
        *,
        export_output_dir: str | Path | None,
        export_allowed_roots: Sequence[str | Path],
    ) -> dict[str, Any]:
        if not request.enabled:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=tuple(request.blocked_reason_codes),
            )
        handlers: Mapping[str, Callable[[Any], dict[str, Any]]] = {
            "apply_as_selection": self._execute_manga_mask_assistant_apply_as_selection_action,
            "protect_text": self._execute_manga_mask_assistant_protect_text_action,
            "send_to_inpaint": self._execute_manga_mask_assistant_send_to_inpaint_action,
            "export_package": lambda resolved_request: (
                self._execute_manga_mask_assistant_export_package_action(
                    resolved_request,
                    output_dir=export_output_dir,
                    allowed_roots=export_allowed_roots,
                )
            ),
            "create_changeset": self._execute_manga_mask_assistant_create_changeset_action,
        }
        handler = handlers.get(request.action)
        if handler is None:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_assistant_action_not_implemented",),
            )
        return handler(request)

    def _execute_manga_mask_assistant_apply_as_selection_action(
        self,
        request: Any,
    ) -> dict[str, Any]:
        package = self._last_manga_mask_assistant_package
        asset_store = self._last_asset_store
        canvas = _manga_mask_assistant_host_canvas(self.host_api)

        if package is None:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_assistant_package_missing",),
            )
        elif asset_store is None:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_selection_asset_store_missing",),
            )
        elif canvas is None:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_selection_canvas_missing",),
            )
        else:
            canvas_size = _canvas_size(canvas)
            if canvas_size is None:
                payload = _manga_mask_assistant_action_result_from_request(
                    request,
                    blocked_reason_codes=("manga_mask_selection_canvas_size_missing",),
                )
            else:
                payload = self._execute_manga_mask_assistant_apply_ready_selection(
                    request,
                    package,
                    asset_store,
                    canvas,
                    canvas_size,
                )
        return payload

    def _execute_manga_mask_assistant_apply_ready_selection(
        self,
        request: Any,
        package: Sam3ComicMaskPackage,
        asset_store: Sam3AssetStore,
        canvas: Sam3SelectionCanvasSubmitter,
        canvas_size: tuple[int, int],
    ) -> dict[str, Any]:
        try:
            plan = build_manga_mask_selection_apply_plan(
                package,
                request.row_id,
                source_width=canvas_size[0],
                source_height=canvas_size[1],
                operation=self.settings.apply_operation,
            )
        except Exception as exc:  # noqa: BLE001 - scrubbed action failure.
            logger.debug(
                "SAM3 Manga Mask Assistant selection plan failed: %s",
                type(exc).__name__,
            )
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                status="failed",
                error_type=type(exc).__name__,
            )
        else:
            payload = self._execute_manga_mask_assistant_selection_plan(
                request,
                plan,
                asset_store,
                canvas,
            )
        return payload

    def _execute_manga_mask_assistant_selection_plan(
        self,
        request: Any,
        plan: Sam3SelectionApplyPlan,
        asset_store: Sam3AssetStore,
        canvas: Sam3SelectionCanvasSubmitter,
    ) -> dict[str, Any]:
        summary = _manga_mask_assistant_selection_apply_summary(plan)
        if not plan.ready:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=tuple(reason.code for reason in plan.blocked_reasons),
                selection_apply=summary,
            )
        else:
            try:
                result = apply_selection_plan(
                    plan,
                    asset_store,
                    canvas,
                    source="sam3.manga_mask_assistant.apply_as_selection",
                )
            except Exception as exc:  # noqa: BLE001 - scrubbed action failure.
                logger.debug(
                    "SAM3 Manga Mask Assistant selection apply failed: %s",
                    type(exc).__name__,
                )
                payload = _manga_mask_assistant_action_result_from_request(
                    request,
                    status="failed",
                    error_type=type(exc).__name__,
                    selection_apply=summary,
                )
            else:
                payload = self._manga_mask_assistant_selection_apply_result_payload(
                    request,
                    plan,
                    result,
                )
        return payload

    def _manga_mask_assistant_selection_apply_result_payload(
        self,
        request: Any,
        plan: Sam3SelectionApplyPlan,
        result: Sam3SelectionApplyExecutionResult,
    ) -> dict[str, Any]:
        if result.applied:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                status="applied",
                handled=True,
                selection_apply=_manga_mask_assistant_selection_apply_summary(
                    plan,
                    applied=True,
                    submitted=result.submitted,
                ),
            )
            self._emit(
                "sam3_ai_select.manga_mask_selection_applied",
                {
                    "schemaVersion": _MANGA_MASK_ASSISTANT_ACTION_RESULT_SCHEMA_VERSION,
                    "tool": TOOL_ID,
                    "status": "applied",
                    "handled": True,
                    "rowId": request.row_id,
                    "candidateId": request.candidate_id,
                    "operation": plan.operation,
                    "selectionBounds": (
                        list(plan.selection_bounds) if plan.selection_bounds is not None else None
                    ),
                    "blockedReasonCodes": [],
                },
            )
        else:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=tuple(reason.code for reason in result.blocked_reasons),
                selection_apply=_manga_mask_assistant_selection_apply_summary(
                    plan,
                    applied=False,
                    submitted=result.submitted,
                ),
            )
        return payload

    def _execute_manga_mask_assistant_protect_text_action(
        self,
        request: Any,
    ) -> dict[str, Any]:
        package = self._last_manga_mask_assistant_package
        asset_store = self._last_asset_store
        canvas = _manga_mask_assistant_host_canvas(self.host_api)

        if package is None:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_assistant_package_missing",),
            )
        elif asset_store is None:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_protect_asset_store_missing",),
            )
        elif canvas is None:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_protect_canvas_missing",),
            )
        else:
            canvas_size = _canvas_size(canvas)
            if canvas_size is None:
                payload = _manga_mask_assistant_action_result_from_request(
                    request,
                    blocked_reason_codes=("manga_mask_protect_canvas_size_missing",),
                )
            else:
                payload = self._execute_manga_mask_assistant_ready_protect_text_action(
                    request,
                    package,
                    asset_store,
                    canvas,
                    canvas_size,
                )
        return payload

    def _execute_manga_mask_assistant_ready_protect_text_action(
        self,
        request: Any,
        package: Sam3ComicMaskPackage,
        asset_store: Sam3AssetStore,
        canvas: object,
        canvas_size: tuple[int, int],
    ) -> dict[str, Any]:
        protect_text_summary: dict[str, Any] | None = None
        try:
            plan = build_speech_bubble_protect_plan(package)
            protect_text_summary = _manga_mask_assistant_protect_text_summary(
                plan,
                canvas_size=canvas_size,
            )
            if not plan.ready:
                return _manga_mask_assistant_action_result_from_request(
                    request,
                    blocked_reason_codes=plan.blocked_reason_codes,
                    protect_text=protect_text_summary,
                )

            result = combine_speech_bubble_protect_mask_assets(
                asset_store,
                plan,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
            )
            if not result.ready:
                return _manga_mask_assistant_action_result_from_request(
                    request,
                    blocked_reason_codes=result.blocked_reason_codes,
                    protect_text=protect_text_summary,
                )

            protected_asset = save_speech_bubble_protect_mask_asset(
                asset_store,
                result,
                output_asset_id=_manga_mask_assistant_protect_text_asset_id(package),
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                source_candidate_id=request.candidate_id,
                metadata={
                    "callerSource": "sam3_manga_mask_assistant",
                    "callerAction": "protect_text",
                    "packageId": package.package_id,
                    "sourcePageId": package.source_page_id,
                    "triggerCandidateId": request.candidate_id,
                },
            )
        except Sam3SpeechBubbleProtectMaskAssetError as exc:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=(exc.code,),
                protect_text=protect_text_summary,
            )
        except Exception as exc:  # noqa: BLE001 - scrubbed action failure.
            logger.debug(
                "SAM3 Manga Mask Assistant text protect failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_action_result_from_request(
                request,
                status="failed",
                error_type=type(exc).__name__,
                protect_text=protect_text_summary,
            )

        protect_text_summary = _manga_mask_assistant_protect_text_summary(
            plan,
            canvas_size=canvas_size,
            protected_asset=protected_asset,
            saved=True,
        )
        selection_applied, selection_blocked_codes = self._apply_protect_mask_as_selection(
            package,
            protected_asset,
            asset_store,
            canvas,
            canvas_size,
        )
        # Additive keys are attached only to the post-save summary; blocked
        # (pre-save) summaries keep their legacy shape.
        protect_text_summary["selectionApplied"] = selection_applied
        if selection_applied:
            protect_text_summary["selectionOperation"] = "replace"
        else:
            protect_text_summary["selectionBlockedReasonCodes"] = list(selection_blocked_codes)
        status = "applied" if selection_applied else "prepared"
        payload = _manga_mask_assistant_action_result_from_request(
            request,
            status=status,
            handled=True,
            protect_text=protect_text_summary,
        )
        self._emit(
            "sam3_ai_select.manga_mask_text_protected",
            {
                "schemaVersion": _MANGA_MASK_ASSISTANT_ACTION_RESULT_SCHEMA_VERSION,
                "tool": TOOL_ID,
                "status": status,
                "handled": True,
                "rowId": request.row_id,
                "candidateId": request.candidate_id,
                "sourceMaskCount": len(plan.mask_asset_ids),
                "protectedMaskAssetId": protected_asset.asset_id,
                "protectedMaskBounds": list(protected_asset.bbox),
                "selectionApplied": selection_applied,
                "selectionBlockedReasonCodes": list(selection_blocked_codes),
                "blockedReasonCodes": [],
            },
        )
        return payload

    def _apply_protect_mask_as_selection(
        self,
        package: Sam3ComicMaskPackage,
        protected_asset: Any,
        asset_store: Sam3AssetStore,
        canvas: object,
        canvas_size: tuple[int, int],
    ) -> tuple[bool, list[str]]:
        """Apply the saved protect mask as the selection (replace). Never raises.

        Returns (applied, blocked_reason_codes); any failure falls back to the
        legacy save-only outcome without losing the saved asset.
        """

        try:
            if not callable(getattr(canvas, "submit", None)):
                return (False, ["canvas_submit_rejected"])
            plan = build_protect_mask_selection_apply_plan(
                package,
                protected_asset,
                source_width=canvas_size[0],
                source_height=canvas_size[1],
            )
            if not plan.ready:
                return (False, [reason.code for reason in plan.blocked_reasons])
            result = apply_selection_plan(
                plan,
                asset_store,
                canvas,  # type: ignore[arg-type]
                source="sam3.manga_mask_assistant.protect_text",
            )
            if not result.applied:
                return (False, [reason.code for reason in result.blocked_reasons])
            return (True, [])
        except Exception as exc:  # noqa: BLE001 - scrubbed action failure.
            logger.debug(
                "SAM3 Manga Mask Assistant protect selection apply failed: %s",
                type(exc).__name__,
            )
            return (False, ["manga_mask_protect_selection_apply_failed"])

    def _execute_manga_mask_assistant_send_to_inpaint_action(
        self,
        request: Any,
    ) -> dict[str, Any]:
        package = self._last_manga_mask_assistant_package
        asset_store = self._last_asset_store
        self._last_manga_inpaint_preparation = None
        self._last_manga_inpaint_preview_request = None
        self._last_manga_inpaint_preview_submit_result = None
        self._last_manga_inpaint_preview_history_metadata = None

        if package is None:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_assistant_package_missing",),
            )
        elif asset_store is None:
            payload = _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_inpaint_asset_store_missing",),
            )
        else:
            candidate = _manga_mask_assistant_candidate_for_request(package, request)
            if candidate is None:
                payload = _manga_mask_assistant_action_result_from_request(
                    request,
                    blocked_reason_codes=("manga_mask_assistant_action_target_missing",),
                )
            else:
                payload = self._execute_manga_mask_assistant_ready_send_to_inpaint_action(
                    request,
                    package,
                    candidate,
                    asset_store,
                )
                payload = self._maybe_escalate_manga_inpaint_submit(request, payload)
        return payload

    def _maybe_escalate_manga_inpaint_submit(
        self,
        request: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Escalate a prepared send_to_inpaint to the async submit executor.

        Escalation happens only when the preview request was actually
        prepared (stored in ``_last_manga_inpaint_preview_request``) and the
        host exposes a generation submitter; otherwise the legacy prepared
        result is returned unchanged.
        """

        if payload.get("status") != "prepared":
            return payload
        preview_summary = _mapping(payload.get("inpaintPreviewRequest"))
        if not preview_summary or not preview_summary.get("prepared"):
            return payload
        if self._resolve_generation_submitter() is None:
            return payload
        if self._preview_submit_in_flight:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("ai_select_preview_submit_in_flight",),
                inpaint_preview_request=preview_summary,
            )
        sentinel_payload = _manga_mask_assistant_action_result_from_request(
            request,
            blocked_reason_codes=(_MANGA_INPAINT_SUBMIT_ASYNC_BLOCKED_REASON,),
            inpaint_preview_request=preview_summary,
        )
        sentinel_payload["previewRequestPrepared"] = True
        return sentinel_payload

    def _execute_manga_mask_assistant_export_package_action(
        self,
        request: Any,
        *,
        output_dir: str | Path | None,
        allowed_roots: Sequence[str | Path],
    ) -> dict[str, Any]:
        package = self._last_manga_mask_assistant_package
        asset_store = self._last_asset_store
        if package is None:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_assistant_package_missing",),
            )
        if asset_store is None:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_export_asset_store_missing",),
            )
        if output_dir is None or not str(output_dir).strip():
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_export_output_dir_missing",),
            )

        try:
            export_result = export_comic_mask_package(
                package,
                asset_store,
                output_dir,
                allowed_roots=allowed_roots,
            )
        except Sam3ComicMaskPackageExportError as exc:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=(exc.code,),
            )
        except Exception as exc:  # noqa: BLE001 - scrubbed action failure.
            logger.debug(
                "SAM3 Manga Mask Assistant package export failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_action_result_from_request(
                request,
                status="failed",
                error_type=type(exc).__name__,
            )

        export_summary = _manga_mask_assistant_export_package_summary(export_result)
        payload = _manga_mask_assistant_action_result_from_request(
            request,
            status="exported",
            handled=True,
            export_package=export_summary,
        )
        self._emit(
            "sam3_ai_select.manga_mask_package_exported",
            {
                "schemaVersion": _MANGA_MASK_ASSISTANT_ACTION_RESULT_SCHEMA_VERSION,
                "tool": TOOL_ID,
                "status": "exported",
                "handled": True,
                "packageId": package.package_id,
                "exportPackage": dict(export_summary),
                "blockedReasonCodes": [],
            },
        )
        return payload

    def _execute_manga_mask_assistant_create_changeset_action(
        self,
        request: Any,
    ) -> dict[str, Any]:
        package = self._last_manga_mask_assistant_package
        asset_store = self._last_asset_store
        if package is None:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_assistant_package_missing",),
            )
        if asset_store is None:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_changeset_asset_store_missing",),
            )

        candidate = _manga_mask_assistant_candidate_for_request(package, request)
        if candidate is None:
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_assistant_action_target_missing",),
            )
        if not _manga_mask_assistant_mask_asset_available(
            asset_store,
            candidate.mask_asset_id,
        ):
            return _manga_mask_assistant_action_result_from_request(
                request,
                blocked_reason_codes=("manga_mask_changeset_mask_asset_missing",),
            )

        try:
            changeset_request = _manga_mask_assistant_changeset_request_summary(
                package,
                request,
                candidate,
            )
        except Exception as exc:  # noqa: BLE001 - scrubbed action failure.
            logger.debug(
                "SAM3 Manga Mask Assistant changeset request preparation failed: %s",
                type(exc).__name__,
            )
            return _manga_mask_assistant_action_result_from_request(
                request,
                status="failed",
                error_type=type(exc).__name__,
            )

        payload = _manga_mask_assistant_action_result_from_request(
            request,
            status="prepared",
            handled=True,
        )
        payload["changesetRequest"] = dict(changeset_request)
        self._emit(
            "sam3_ai_select.manga_mask_changeset_prepared",
            {
                "schemaVersion": _MANGA_MASK_ASSISTANT_ACTION_RESULT_SCHEMA_VERSION,
                "tool": TOOL_ID,
                "status": "prepared",
                "handled": True,
                "packageId": package.package_id,
                "sourcePageId": package.source_page_id,
                "changesetRequest": dict(changeset_request),
                "blockedReasonCodes": [],
            },
        )
        return payload

    def _execute_manga_mask_assistant_ready_send_to_inpaint_action(
        self,
        request: Any,
        package: Sam3ComicMaskPackage,
        candidate: Any,
        asset_store: Sam3AssetStore,
    ) -> dict[str, Any]:
        payload, preparation, preview_summary = self._manga_inpaint_send_to_inpaint_prepare(
            request,
            package,
            candidate,
            asset_store,
        )
        if preparation is not None:
            self._emit(
                "sam3_ai_select.manga_inpaint_prepared",
                {
                    "schemaVersion": _MANGA_MASK_ASSISTANT_ACTION_RESULT_SCHEMA_VERSION,
                    "tool": TOOL_ID,
                    "status": "prepared",
                    "handled": True,
                    "rowId": request.row_id,
                    "candidateId": request.candidate_id,
                    "category": preparation.category,
                    "protectedMaskAssetId": preparation.protected_mask_asset_id,
                    "previewRequestPrepared": bool(
                        preview_summary and preview_summary.get("prepared")
                    ),
                    "blockedReasonCodes": [],
                },
            )
        return payload

    def _manga_inpaint_send_to_inpaint_prepare(
        self,
        request: Any,
        package: Sam3ComicMaskPackage,
        candidate: Any,
        asset_store: Sam3AssetStore,
    ) -> tuple[dict[str, Any], Sam3MangaInpaintPreparation | None, dict[str, Any] | None]:
        """Prepare a manga inpaint preview request without emitting events.

        Shared by the synchronous ready handler (which emits
        ``manga_inpaint_prepared``) and the bound async submit re-prepare
        (which must stay event-free).
        """

        try:
            inpaint_mask_available = _manga_mask_assistant_mask_asset_available(
                asset_store,
                candidate.mask_asset_id,
            )
            protected_mask_asset_id = _manga_mask_assistant_protect_text_asset_id(package)
            protected_mask_available = _manga_mask_assistant_mask_asset_available(
                asset_store,
                protected_mask_asset_id,
            )
            preparation = build_manga_inpaint_preparation(
                candidate,
                protected_mask_asset_id=(
                    protected_mask_asset_id if protected_mask_available else None
                ),
            )
            preparation = _manga_mask_assistant_preparation_with_asset_readiness(
                preparation,
                inpaint_mask_available=inpaint_mask_available,
            )
            summary = _manga_mask_assistant_inpaint_preparation_summary(
                preparation,
                inpaint_mask_available=inpaint_mask_available,
                protected_mask_available=protected_mask_available,
            )
        except Exception as exc:  # noqa: BLE001 - scrubbed action failure.
            logger.debug(
                "SAM3 Manga Mask Assistant inpaint preparation failed: %s",
                type(exc).__name__,
            )
            return (
                _manga_mask_assistant_action_result_from_request(
                    request,
                    status="failed",
                    error_type=type(exc).__name__,
                ),
                None,
                None,
            )

        if not preparation.ready:
            return (
                _manga_mask_assistant_action_result_from_request(
                    request,
                    blocked_reason_codes=preparation.blocked_reason_codes,
                    inpaint_preparation=summary,
                ),
                None,
                None,
            )

        self._last_manga_inpaint_preparation = preparation
        preview_summary = self._prepare_manga_mask_assistant_inpaint_preview_request(
            request,
            package,
            preparation,
        )
        payload = _manga_mask_assistant_action_result_from_request(
            request,
            status="prepared",
            handled=True,
            inpaint_preparation=summary,
            inpaint_preview_request=preview_summary,
        )
        return (payload, preparation, preview_summary)

    def _prepare_manga_mask_assistant_inpaint_preview_request(
        self,
        request: Any,
        package: Sam3ComicMaskPackage,
        preparation: Sam3MangaInpaintPreparation,
    ) -> dict[str, Any] | None:
        canvas = _manga_mask_assistant_host_canvas(self.host_api)
        effect_layer_id = _manga_mask_assistant_active_ai_effect_layer_id(canvas)
        if effect_layer_id is None:
            return None

        try:
            preview_request = build_manga_inpaint_preview_request(
                effect_layer_id,
                preparation,
                candidate_set_id=package.package_id,
                trigger="manual",
                metadata={
                    "callerSource": "sam3_manga_mask_assistant",
                    "callerAction": "send_to_inpaint",
                    "packageId": package.package_id,
                    "sourcePageId": package.source_page_id,
                    "triggerCandidateId": request.candidate_id,
                },
            )
        except Sam3MangaInpaintPreviewRequestError as exc:
            return _manga_mask_assistant_inpaint_preview_request_summary(
                prepared=False,
                candidate_id=preparation.candidate_id,
                effect_layer_id=effect_layer_id,
                package_id=package.package_id,
                source_page_id=package.source_page_id,
                blocked_reason_codes=(exc.code,),
            )

        self._last_manga_inpaint_preview_request = dict(preview_request)
        return _manga_mask_assistant_inpaint_preview_request_summary(
            prepared=True,
            candidate_id=preparation.candidate_id,
            effect_layer_id=effect_layer_id,
            package_id=package.package_id,
            source_page_id=package.source_page_id,
            apply_to_canvas=bool(preview_request.get("applyToCanvas", False)),
            persist_overrides=bool(preview_request.get("persistOverrides", False)),
        )

    def get_run_readiness_snapshot(self) -> dict[str, Any]:
        return _run_readiness_snapshot_payload(self.settings)

    async def get_bound_run_readiness_snapshot(
        self,
        *,
        binding: Sam3ProviderBinding | None = None,
    ) -> dict[str, Any]:
        prompt_readiness = self.get_run_readiness_snapshot()
        if not prompt_readiness["ready"]:
            return _bound_run_readiness_snapshot_payload(prompt_readiness)

        provider_binding = await self._resolve_provider_binding(binding)
        return _bound_run_readiness_snapshot_payload(
            prompt_readiness,
            binding=provider_binding,
        )

    def get_context_actions_snapshot(self) -> dict[str, Any]:
        return _context_actions_snapshot_payload(
            self._last_session,
            self._last_overlay_model,
            self._last_asset_store,
            self._resolve_generation_history_store(None),
            self._last_run_status,
            self.settings,
            candidate_overlay_visible=self._candidate_overlay_visible,
        )

    async def get_bound_context_actions_snapshot(
        self,
        *,
        binding: Sam3ProviderBinding | None = None,
    ) -> dict[str, Any]:
        provider_binding = binding
        if provider_binding is None and self.get_run_readiness_snapshot()["ready"]:
            provider_binding = await self._resolve_provider_binding()
        snapshot = self.get_context_actions_snapshot()
        bound_readiness = await self.get_bound_run_readiness_snapshot(binding=provider_binding)
        return _bound_context_actions_snapshot_payload(
            snapshot,
            bound_readiness,
            self._last_run_status,
            provider_binding,
        )

    async def execute_bound_context_action(  # noqa: PLR0911 - flat action dispatcher
        self,
        action_id: str,
        *,
        binding: Sam3ProviderBinding | None = None,
        host_api: HostAPI | None = None,
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        canvas: Sam3SelectionCanvasSubmitter | None = None,
        source: str | None = "sam3.selection.apply",
        merge_key: str | None = None,
        asset_store: Sam3AssetStore | None = None,
        history_store: object | None = None,
        selected_candidate_ids: Iterable[str] | None = None,
        combine_mode: object | None = None,
        effect_layer_id: str | None = None,
        slot_id: str | None = None,
        exclude_blend: bool = True,
        force_regenerate: bool = False,
        preview_param_values: Mapping[str, object] | None = None,
        preview_slot_refs: Mapping[str, object] | None = None,
        preview_param_ui_state: Mapping[str, object] | None = None,
        preview_replace_param_values: bool = False,
        preview_replace_slot_refs: bool = False,
        preview_replace_param_ui_state: bool = False,
        preview_trigger: str | None = None,
        preview_target_server_id: str | None = None,
        preview_output_mode_override: str | None = None,
        preview_metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        if action_id == _AUTO_RUN_ACTION_ID:
            return await self._execute_bound_auto_run_action(
                binding=binding,
                host_api=host_api,
                request_id=request_id,
                metadata=metadata,
                canvas=canvas,
            )
        if _is_preview_submit_context_action_id(action_id):
            return await self._execute_bound_preview_submit_action(
                action_id,
                canvas=canvas,
                asset_store=asset_store,
                history_store=history_store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                force_regenerate=force_regenerate,
                preview_param_values=preview_param_values,
                preview_slot_refs=preview_slot_refs,
                preview_param_ui_state=preview_param_ui_state,
                preview_replace_param_values=preview_replace_param_values,
                preview_replace_slot_refs=preview_replace_slot_refs,
                preview_replace_param_ui_state=preview_replace_param_ui_state,
                preview_trigger=preview_trigger,
                preview_target_server_id=preview_target_server_id,
                preview_output_mode_override=preview_output_mode_override,
                preview_metadata=preview_metadata,
            )
        if _is_manga_inpaint_submit_bound_action_id(action_id):
            return await self._execute_bound_manga_inpaint_submit_action(action_id)
        if action_id in _CONTEXT_EXPORT_ACTION_IDS:
            # Export actions open a modal file dialog: agent/bound routes
            # (Canvas Copilot / MCP bindings) must never reach it.
            return _asset_export_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                scope=_asset_export_scope_for_action_id(action_id),
                blocked_reason_codes=["asset_export_ui_required"],
            )
        is_refine_action = _is_refine_context_action_id(action_id)
        if action_id != _RUN_ACTION_ID and not is_refine_action:
            return self.execute_context_action(
                action_id,
                canvas=canvas,
                source=source,
                merge_key=merge_key,
                asset_store=asset_store,
                history_store=history_store,
                selected_candidate_ids=selected_candidate_ids,
                combine_mode=combine_mode,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                exclude_blend=exclude_blend,
                force_regenerate=force_regenerate,
                preview_param_values=preview_param_values,
                preview_slot_refs=preview_slot_refs,
                preview_param_ui_state=preview_param_ui_state,
                preview_replace_param_values=preview_replace_param_values,
                preview_replace_slot_refs=preview_replace_slot_refs,
                preview_replace_param_ui_state=preview_replace_param_ui_state,
                preview_trigger=preview_trigger,
                preview_target_server_id=preview_target_server_id,
                preview_output_mode_override=preview_output_mode_override,
                preview_metadata=preview_metadata,
            )

        provider_binding = binding
        if provider_binding is None and (
            is_refine_action or self.get_run_readiness_snapshot()["ready"]
        ):
            provider_binding = await self._resolve_provider_binding()

        snapshot = await self.get_bound_context_actions_snapshot(binding=provider_binding)
        action = _context_action_by_id(snapshot, action_id)
        if action is None:
            return _context_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )
        if not action.get("enabled", False):
            blocked_reason_codes = list(action.get("blockedReasonCodes", []))
            return self._bound_async_action_blocked_result(
                action_id,
                blocked_reason_codes,
            )
        if is_refine_action:
            candidate_id = _candidate_id_from_refine_action_id(action_id)
            return await self._execute_bound_refine_context_action(
                provider_binding,
                action_id=action_id,
                request_id=request_id,
                candidate_id=candidate_id,
                metadata=metadata,
            )
        return await self.execute_bound_host_source_run_action(
            binding=provider_binding,
            host_api=host_api,
            request_id=request_id,
            metadata=metadata,
        )

    async def _execute_bound_preview_submit_action(
        self,
        action_id: str,
        *,
        canvas: Sam3SelectionCanvasSubmitter | None,
        asset_store: Sam3AssetStore | None,
        history_store: object | None,
        effect_layer_id: str | None,
        slot_id: str | None,
        force_regenerate: bool,
        preview_param_values: Mapping[str, object] | None,
        preview_slot_refs: Mapping[str, object] | None,
        preview_param_ui_state: Mapping[str, object] | None,
        preview_replace_param_values: bool,
        preview_replace_slot_refs: bool,
        preview_replace_param_ui_state: bool,
        preview_trigger: str | None,
        preview_target_server_id: str | None,
        preview_output_mode_override: str | None,
        preview_metadata: Mapping[str, object] | None,
    ) -> dict[str, Any]:
        submit_generation = self._resolve_generation_submitter()
        if submit_generation is None:
            # No submitter exposed by the host: keep the legacy synchronous
            # prepare-only behavior (the dispatcher sentinel stays silent too).
            return self.execute_context_action(
                action_id,
                canvas=canvas,
                asset_store=asset_store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                force_regenerate=force_regenerate,
                preview_param_values=preview_param_values,
                preview_slot_refs=preview_slot_refs,
                preview_param_ui_state=preview_param_ui_state,
                preview_replace_param_values=preview_replace_param_values,
                preview_replace_slot_refs=preview_replace_slot_refs,
                preview_replace_param_ui_state=preview_replace_param_ui_state,
                preview_trigger=preview_trigger,
                preview_target_server_id=preview_target_server_id,
                preview_output_mode_override=preview_output_mode_override,
                preview_metadata=preview_metadata,
            )
        if self._preview_submit_in_flight:
            return _ai_effect_preview_submit_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_preview_submit_in_flight"],
            )
        self._preview_submit_in_flight = True
        # Status-bar indicator hook; paired with ai_effect_preview_submitted.
        self._emit(
            "sam3_ai_select.preview_submit_started",
            {"tool": TOOL_ID, "actionId": action_id},
        )
        self._last_preview_submit_diff_qa_ok = None
        before, before_blocked_code = self._capture_generation_diff_before_png()
        constrain_mask_asset_id = self._resolve_preview_action_constrain_mask_asset_id(action_id)
        if action_id == _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT:
            # Selection previews never depend on a run: resolve the diff QA
            # store with the same default-root fallback the prepare uses, or
            # the constrain would silently die without a prior run.
            try:
                diff_qa_store: Sam3AssetStore | None = self._resolve_selection_asset_store(
                    asset_store
                )
            except Sam3AISelectToolStateError:
                diff_qa_store = None
        else:
            diff_qa_store = asset_store if asset_store is not None else self._last_asset_store
        collected_assets: list[Mapping[str, object]] = []
        try:
            result = await self.execute_ai_effect_preview_submit_action(
                action_id,
                submit_generation,
                canvas=canvas,
                asset_store=asset_store,
                history_store=history_store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                force_regenerate=force_regenerate,
                preview_param_values=preview_param_values,
                preview_slot_refs=preview_slot_refs,
                preview_param_ui_state=preview_param_ui_state,
                preview_replace_param_values=preview_replace_param_values,
                preview_replace_slot_refs=preview_replace_slot_refs,
                preview_replace_param_ui_state=preview_replace_param_ui_state,
                preview_trigger=preview_trigger,
                preview_target_server_id=preview_target_server_id,
                preview_output_mode_override=preview_output_mode_override,
                preview_metadata=preview_metadata,
                result_asset_collector=collected_assets.extend,
            )
            if _clean_optional_text(result.get("status")) == "submitted":
                diff_qa = self._run_generation_diff_qa_step(
                    before=before,
                    before_blocked_code=before_blocked_code,
                    after_assets=collected_assets,
                    asset_store=diff_qa_store,
                    protected_mask_asset_id=None,
                    constrain_mask_asset_id=constrain_mask_asset_id,
                )
                result = dict(result)
                result["diffQa"] = diff_qa
                self._last_preview_submit_diff_qa_ok = diff_qa.get("ok")
                preview_history = _mapping(result.get("previewHistory"))
                self._record_generation_diff_qa_history_snapshot(
                    (
                        _clean_optional_text(preview_history.get("historyJobId"))
                        if preview_history is not None
                        else None
                    ),
                    diff_qa,
                )
        finally:
            self._preview_submit_in_flight = False
            collected_assets.clear()
        status = _clean_optional_text(result.get("status")) or "failed"
        terminal_status = status if status in {"submitted", "blocked"} else "failed"
        blocked_reason_codes = _string_list(result.get("blockedReasonCodes"))
        self._last_preview_submit_status = terminal_status
        self._last_preview_submit_blocked_reason_codes = blocked_reason_codes
        self._last_preview_submit_state = Sam3AsyncSubmitState.from_submit_result(
            action_id, result, cancel_supported=self._resolve_generation_canceller() is not None
        )
        self._emit(
            "sam3_ai_select.ai_effect_preview_submitted",
            {
                "actionId": action_id,
                "status": terminal_status,
                "blockedReasonCodes": blocked_reason_codes,
                # previewRequest keeps candidate/layer identity even on failed
                # submits, where previewSubmit is None.
                "previewRequest": _mapping(result.get("previewRequest")),
                "previewSubmit": _mapping(result.get("previewSubmit")),
                "diffQa": _mapping(result.get("diffQa")),
            },
        )
        self._refresh_candidate_list_panel()
        return result

    async def _execute_bound_manga_inpaint_submit_action(self, action_id: str) -> dict[str, Any]:
        submit_generation = self._resolve_generation_submitter()
        if submit_generation is None:
            return self._finish_manga_inpaint_submit_terminal(
                _manga_mask_assistant_inpaint_preview_submit_action_result_payload(
                    action_id,
                    status="blocked",
                    handled=False,
                    blocked_reason_codes=("generation_submitter_missing",),
                ),
                emit_event=True,
            )
        if self._preview_submit_in_flight:
            return self._finish_manga_inpaint_submit_terminal(
                _manga_mask_assistant_inpaint_preview_submit_action_result_payload(
                    action_id,
                    status="blocked",
                    handled=False,
                    blocked_reason_codes=("ai_select_preview_submit_in_flight",),
                ),
                emit_event=True,
            )
        self._preview_submit_in_flight = True
        # Status-bar indicator hook; paired with manga_inpaint_preview_submitted.
        self._emit(
            "sam3_ai_select.preview_submit_started",
            {"tool": TOOL_ID, "actionId": action_id},
        )
        self._last_manga_inpaint_submit_diff_qa_ok = None
        collected_assets: list[Mapping[str, object]] = []
        try:
            reprepare_blocked = self._reprepare_manga_inpaint_preview_request(action_id)
            if reprepare_blocked is not None:
                return self._finish_manga_inpaint_submit_terminal(
                    reprepare_blocked,
                    emit_event=True,
                )
            # Snapshot mask ids locally before the await: the instance state
            # can be replaced by other tasks while the submit is in flight.
            preparation = self._last_manga_inpaint_preparation
            protected_mask_asset_id = (
                preparation.protected_mask_asset_id if preparation is not None else None
            )
            constrain_mask_asset_id = (
                preparation.inpaint_mask_asset_id if preparation is not None else None
            )
            before, before_blocked_code = self._capture_generation_diff_before_png()
            result = await self.execute_manga_mask_assistant_inpaint_preview_submit_action(
                submit_generation,
                action_id=action_id,
                result_asset_collector=collected_assets.extend,
            )
            self._last_manga_inpaint_preview_request = None
            if _clean_optional_text(result.get("status")) == "submitted":
                history_result = (
                    self.execute_manga_mask_assistant_inpaint_preview_history_record_action(
                        history_store=self._resolve_generation_history_store(None),
                        action_id=action_id,
                    )
                )
                result = dict(result)
                result["previewHistory"] = _mapping(history_result.get("previewHistory"))
                diff_qa = self._run_generation_diff_qa_step(
                    before=before,
                    before_blocked_code=before_blocked_code,
                    after_assets=collected_assets,
                    asset_store=self._last_asset_store,
                    protected_mask_asset_id=protected_mask_asset_id,
                    constrain_mask_asset_id=constrain_mask_asset_id,
                )
                result["diffQa"] = diff_qa
                self._last_manga_inpaint_submit_diff_qa_ok = diff_qa.get("ok")
                preview_history = _mapping(result.get("previewHistory"))
                self._record_generation_diff_qa_history_snapshot(
                    (
                        _clean_optional_text(preview_history.get("historyJobId"))
                        if preview_history is not None
                        else None
                    ),
                    diff_qa,
                )
        finally:
            self._preview_submit_in_flight = False
            collected_assets.clear()
        return self._finish_manga_inpaint_submit_terminal(result, emit_event=False)

    def _reprepare_manga_inpaint_preview_request(  # noqa: PLR0911 - flat guard chain
        self,
        action_id: str,
    ) -> dict[str, Any] | None:
        """Re-prepare the preview request from the action id, event-free.

        Returns None on success (the fresh request is stored in
        ``_last_manga_inpaint_preview_request``); returns a blocked submit
        action result payload otherwise. Never reuses previously stored
        request/preparation state.
        """

        self._last_manga_inpaint_preview_request = None
        self._last_manga_inpaint_preparation = None

        def _blocked(codes: Sequence[str], error_type: str | None = None) -> dict[str, Any]:
            return _manga_mask_assistant_inpaint_preview_submit_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=tuple(codes),
                error_type=error_type,
            )

        package = self._last_manga_mask_assistant_package
        if package is None:
            return _blocked(("manga_mask_assistant_package_missing",))
        asset_store = self._last_asset_store
        if asset_store is None:
            return _blocked(("manga_mask_inpaint_asset_store_missing",))
        target = parse_manga_mask_assistant_action_id(action_id)
        if target is None or target.action != _MANGA_MASK_ASSISTANT_SEND_TO_INPAINT_ACTION:
            return _blocked(("manga_mask_assistant_action_id_invalid",))
        request = _manga_mask_assistant_resolve_action_request(action_id, package, target)
        if isinstance(request, Mapping):
            return _blocked(
                tuple(_string_list(request.get("blockedReasonCodes")))
                or ("manga_mask_assistant_action_target_missing",),
                error_type=_clean_optional_text(request.get("errorType")),
            )
        if not request.enabled:
            return _blocked(tuple(request.blocked_reason_codes))
        candidate = _manga_mask_assistant_candidate_for_request(package, request)
        if candidate is None:
            return _blocked(("manga_mask_assistant_action_target_missing",))
        payload, preparation, preview_summary = self._manga_inpaint_send_to_inpaint_prepare(
            request,
            package,
            candidate,
            asset_store,
        )
        if preparation is None or not (preview_summary and preview_summary.get("prepared")):
            codes = _string_list(payload.get("blockedReasonCodes"))
            if not codes and preview_summary is not None:
                codes = _string_list(preview_summary.get("blockedReasonCodes"))
            return _blocked(
                tuple(codes) or ("manga_inpaint_preview_request_missing",),
                error_type=_clean_optional_text(payload.get("errorType")),
            )
        return None

    def _finish_manga_inpaint_submit_terminal(
        self,
        result: dict[str, Any],
        *,
        emit_event: bool,
    ) -> dict[str, Any]:
        """Common terminal tail for bound manga inpaint submits.

        Updates the submit status state for panel snapshots, emits the
        submit event for bound-only terminals that never reached the submit
        action method (which emits for its own terminals), and refreshes the
        panel.
        """

        status = _clean_optional_text(result.get("status")) or "failed"
        terminal_status = status if status in {"submitted", "blocked"} else "failed"
        self._last_manga_inpaint_submit_status = terminal_status
        self._last_manga_inpaint_submit_blocked_reason_codes = _string_list(
            result.get("blockedReasonCodes")
        )
        if emit_event:
            self._emit_manga_inpaint_preview_submitted(result)
        self._refresh_manga_mask_assistant_panel()
        return result

    def _resolve_preview_action_constrain_mask_asset_id(self, action_id: str) -> str | None:
        """Resolve the inpaint mask asset id of the preview action's candidate.

        Session-based (the preview request payload deliberately never carries
        mask asset ids); snapshot before the submit await so a concurrent
        selection change cannot swap the candidate. Never raises.
        """

        try:
            if action_id == _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT:
                # Selection-derived submits constrain to the bound selection
                # asset, never to the (possibly unrelated) session candidate.
                return _clean_optional_text(self._last_selection_inpaint_mask_asset_id)
            session = self._last_session
            if session is None:
                return None
            candidate_id = _candidate_id_from_preview_inpaint_action_id(action_id)
            if candidate_id is not None:
                candidate = next(
                    (
                        item
                        for item in session.candidate_set.candidates
                        if item.candidate_id == candidate_id
                    ),
                    None,
                )
            else:
                candidate = session.active_candidate
            if candidate is None:
                return None
            return _clean_optional_text(candidate.mask_asset_id)
        except Exception:
            logger.debug("SAM3 diff QA constrain resolution failed", exc_info=True)
            return None

    def _capture_generation_diff_before_png(  # noqa: PLR0911 - flat guard chain
        self,
    ) -> tuple[tuple[bytes, int, int] | None, str | None]:
        """Capture the visible composite for diff QA. Never raises.

        Returns ``((png, width, height), None)`` on success, or
        ``(None, blocked_reason_code)`` when the capture is skipped.
        """

        try:
            canvas = _manga_mask_assistant_host_canvas(self.host_api)
            getter = getattr(canvas, "get_composited_pixmap", None) if canvas is not None else None
            if not callable(getter):
                return (None, "generation_diff_before_unavailable")
            # Guard on the declared canvas size before the composite readback
            # so oversized canvases skip the expensive GPU flush entirely.
            canvas_size = _canvas_size(canvas)
            if (
                canvas_size is not None
                and canvas_size[0] * canvas_size[1] > _GENERATION_DIFF_QA_MAX_CANVAS_PX
            ):
                return (None, "generation_diff_canvas_too_large")
            from PySide6.QtGui import QPixmap  # noqa: PLC0415

            pixmap = getter()
            if not isinstance(pixmap, QPixmap) or pixmap.isNull():
                return (None, "generation_diff_before_unavailable")
            image = pixmap.toImage()
            if image.isNull() or image.width() <= 0 or image.height() <= 0:
                return (None, "generation_diff_before_unavailable")
            if image.width() * image.height() > _GENERATION_DIFF_QA_MAX_CANVAS_PX:
                return (None, "generation_diff_canvas_too_large")
            return (
                (_encode_qimage_to_png_bytes(image), image.width(), image.height()),
                None,
            )
        except Exception:
            logger.debug("SAM3 diff QA before capture failed", exc_info=True)
            return (None, "generation_diff_before_unavailable")

    def _run_generation_diff_qa_step(
        self,
        *,
        before: tuple[bytes, int, int] | None,
        before_blocked_code: str | None,
        after_assets: Sequence[Mapping[str, object]],
        asset_store: Sam3AssetStore | None,
        protected_mask_asset_id: str | None,
        constrain_mask_asset_id: str | None,
    ) -> dict[str, Any]:
        """Run the advisory in-memory diff QA for one submitted preview.

        Never raises and never persists anything to the asset store; every
        failure is reported through the returned payload only.
        """

        try:
            if before is None:
                return _generation_diff_qa_step_payload(
                    blocked_reason_codes=[
                        before_blocked_code or "generation_diff_before_unavailable"
                    ],
                )
            after_png = next(
                (
                    asset.get("pngBytes")
                    for asset in after_assets
                    if isinstance(asset.get("pngBytes"), bytes | bytearray)
                ),
                None,
            )
            if after_png is None:
                return _generation_diff_qa_step_payload(
                    blocked_reason_codes=["generation_diff_after_unavailable"],
                )
            before_png, canvas_width, canvas_height = before
            qa = run_generation_result_diff_qa(
                before_png,
                bytes(after_png),
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                asset_store=asset_store,
                protected_mask_asset_id=protected_mask_asset_id,
                constrain_mask_asset_id=constrain_mask_asset_id,
            )
            return _generation_diff_qa_step_payload(qa=qa)
        except (Sam3GenerationResultDiffError, Sam3GenerationResultDiffQAError) as exc:
            return _generation_diff_qa_step_payload(blocked_reason_codes=[exc.code])
        except Exception:
            logger.debug("SAM3 auto diff QA failed unexpectedly", exc_info=True)
            return _generation_diff_qa_step_payload(
                blocked_reason_codes=["generation_diff_qa_unexpected_error"],
            )

    def _record_generation_diff_qa_history_snapshot(
        self,
        history_job_id: str | None,
        diff_qa: Mapping[str, Any],
    ) -> None:
        """Attach an auto-run diff QA summary to the submit's history record.

        Best-effort: a missing store/record leaves the QA in the submit
        result only and never raises.
        """

        try:
            if not diff_qa.get("ran"):
                return
            clean_history_job_id = _clean_optional_text(history_job_id)
            if clean_history_job_id is None:
                return
            target = self._resolve_generation_diff_qa_history_target(
                "sam3_ai_select.generation_diff_qa.auto",
                history_job_id=clean_history_job_id,
                history_store=None,
            )
            if isinstance(target, dict):
                return
            _resolved_job_id, update_record, record = target
            updated_record = _generation_record_with_generation_diff_qa(
                record,
                diff_qa,
                qa_status="passed" if bool(diff_qa.get("ok")) else "warning",
            )
            update_record(updated_record)
        except Exception:
            # Duck-typed host stores can hand back arbitrary record objects;
            # the auto QA history write must never break a submitted result.
            logger.debug("SAM3 auto diff QA history update failed", exc_info=True)

    def _resolve_generation_submitter(self) -> Callable[..., Any] | None:
        host = self.host_api
        if host is None:
            return None
        get_service = getattr(host, "get_service", None)
        if not callable(get_service):
            return None
        try:
            submitter = get_service("ai_effect_generation_submitter")
        except Exception:
            logger.debug("SAM3 generation submitter service lookup failed", exc_info=True)
            return None
        return submitter if callable(submitter) else None

    def _resolve_generation_canceller(self) -> Callable[[str], bool] | None:
        host = self.host_api
        get_service = getattr(host, "get_service", None) if host is not None else None
        if not callable(get_service):
            return None
        try:
            canceller = get_service("ai_effect_generation_canceller")
        except Exception:
            return None
        return canceller if callable(canceller) else None

    def get_preview_submit_state(self) -> dict[str, Any] | None:
        state = self._last_preview_submit_state
        return state.to_dict() if state is not None else None

    def cancel_last_preview_submit(self) -> dict[str, Any]:
        """CT-23 §7.3: cancel a queued / generating preview job; never revert a commit."""
        bridge = Sam3SubmitCancelBridge(self._resolve_generation_canceller())
        state, result = bridge.cancel(self._last_preview_submit_state)
        self._last_preview_submit_state = state
        payload = result.to_dict()
        payload["state"] = state.to_dict() if state is not None else None
        if result.ok:
            self._emit(
                "sam3_ai_select.preview_submit_cancelled",
                {"tool": TOOL_ID, "actionId": state.action_id if state else None},
            )
        self._refresh_candidate_list_panel()
        return payload

    def resolve_panel_geometry(self, source_kind: str) -> dict[str, Any]:
        """CT-23 §6: panel bboxes from an explicit source (never guessed silently)."""
        geometry = Sam3PanelGeometryProvider(self.host_api).resolve(source_kind)
        payload = geometry.to_dict()
        payload["panelBboxes"] = {
            panel_id: list(bbox) for panel_id, bbox in geometry.to_panel_bboxes().items()
        }
        return payload

    def _bound_async_action_blocked_result(
        self,
        action_id: str,
        blocked_reason_codes: Iterable[str],
    ) -> dict[str, Any]:
        blocked_codes = list(blocked_reason_codes)
        self._last_run_status = _run_status_state_payload(
            "blocked",
            blocked_reason_codes=blocked_codes,
        )
        if _is_refine_context_action_id(action_id):
            return _refine_action_result_payload(
                action_id=action_id,
                status="blocked",
                blocked_reason_codes=blocked_codes,
            )
        return _run_action_result_payload(
            status="blocked",
            blocked_reason_codes=blocked_codes,
        )

    async def _execute_bound_refine_context_action(
        self,
        provider_binding: Sam3ProviderBinding | None,
        *,
        action_id: str,
        request_id: str | None,
        candidate_id: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if provider_binding is None:
            return _refine_action_result_payload(
                action_id=action_id,
                status="blocked",
                blocked_reason_codes=["sam3_provider_not_ready"],
            )
        if candidate_id is not None:
            try:
                if (
                    self._last_session is not None
                    and self._last_session.selected_candidate_id != candidate_id
                ):
                    self.select_candidate(candidate_id)
            except (Sam3AISelectToolStateError, Sam3SelectionSessionError) as exc:
                return _refine_action_result_payload(
                    action_id=action_id,
                    status="blocked",
                    blocked_reason_codes=[exc.code],
                )
        return await self.execute_refine_action(
            provider_binding.provider,
            action_id=action_id,
            request_id=request_id,
            candidate_id=candidate_id,
            metadata=metadata,
        )

    async def execute_ai_effect_preview_submit_action(
        self,
        action_id: str,
        submit_generation: GenerationSubmitter | None,
        *,
        canvas: Sam3SelectionCanvasSubmitter | None = None,
        asset_store: Sam3AssetStore | None = None,
        history_store: object | None = None,
        effect_layer_id: str | None = None,
        slot_id: str | None = None,
        force_regenerate: bool = False,
        preview_param_values: Mapping[str, object] | None = None,
        preview_slot_refs: Mapping[str, object] | None = None,
        preview_param_ui_state: Mapping[str, object] | None = None,
        preview_replace_param_values: bool = False,
        preview_replace_slot_refs: bool = False,
        preview_replace_param_ui_state: bool = False,
        preview_trigger: str | None = None,
        preview_target_server_id: str | None = None,
        preview_output_mode_override: str | None = None,
        preview_metadata: Mapping[str, object] | None = None,
        result_asset_collector: Callable[[Sequence[Mapping[str, object]]], None] | None = None,
    ) -> dict[str, Any]:
        # Re-prepare via the private handlers (not the dispatcher) so the
        # async-executor sentinel in execute_context_action cannot re-enter
        # here. Replicate the dispatcher's asset-store fallback.
        store = asset_store if asset_store is not None else self._last_asset_store
        prepare_kwargs: dict[str, Any] = {
            "canvas": canvas,
            "asset_store": store,
            "effect_layer_id": effect_layer_id,
            "slot_id": slot_id,
            "force_regenerate": force_regenerate,
            "preview_param_values": preview_param_values,
            "preview_slot_refs": preview_slot_refs,
            "preview_param_ui_state": preview_param_ui_state,
            "preview_replace_param_values": preview_replace_param_values,
            "preview_replace_slot_refs": preview_replace_slot_refs,
            "preview_replace_param_ui_state": preview_replace_param_ui_state,
            "preview_trigger": preview_trigger,
            "preview_target_server_id": preview_target_server_id,
            "preview_output_mode_override": preview_output_mode_override,
            "preview_metadata": preview_metadata,
        }
        if action_id == _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT:
            request_result = self._execute_context_selection_preview_request_action(
                action_id,
                **prepare_kwargs,
            )
        elif action_id.startswith(_CONTEXT_CANDIDATE_PREVIEW_INPAINT_ACTION_PREFIX):
            request_result = self._execute_context_candidate_ai_effect_preview_request_action(
                action_id,
                **prepare_kwargs,
            )
        else:
            request_result = self._execute_context_ai_effect_preview_request_action(
                action_id,
                **prepare_kwargs,
            )
        preview_request = _mapping(request_result.get("previewRequest"))
        request_status = _clean_optional_text(request_result.get("status")) or "blocked"
        if request_status != "prepared":
            return _ai_effect_preview_submit_action_result_payload(
                action_id,
                status=request_status,
                handled=bool(request_result.get("handled", False)),
                preview_request=preview_request,
                blocked_reason_codes=_string_list(request_result.get("blockedReasonCodes")),
            )

        request = _mapping(preview_request.get("request") if preview_request else None)
        if request is None:
            return _ai_effect_preview_submit_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                preview_request=preview_request,
                blocked_reason_codes=["preview_request_missing"],
            )
        if not callable(submit_generation):
            return _ai_effect_preview_submit_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                preview_request=preview_request,
                blocked_reason_codes=["generation_submitter_missing"],
            )

        submit_result = await submit_ai_effect_preview_generation_request(
            request,
            submit_generation,
            action_id=action_id,
            candidate_set_id=(
                _clean_optional_text(preview_request.get("candidateSetId"))
                if preview_request is not None
                else None
            ),
            candidate_id=(
                _clean_optional_text(preview_request.get("candidateId"))
                if preview_request is not None
                else None
            ),
            effect_layer_id=(
                _clean_optional_text(preview_request.get("effectLayerId"))
                if preview_request is not None
                else None
            ),
            slot_id=(
                _clean_optional_text(preview_request.get("slotId"))
                if preview_request is not None
                else None
            ),
            result_asset_collector=result_asset_collector,
        )
        error = _mapping(submit_result.get("error"))
        return _ai_effect_preview_submit_action_result_payload(
            action_id,
            status=_clean_optional_text(submit_result.get("status")) or "failed",
            handled=bool(submit_result.get("handled", False)),
            preview_request=preview_request,
            preview_submit=_mapping(submit_result.get("previewSubmit")),
            preview_history=(
                self._record_ai_effect_preview_submit_history(
                    _mapping(submit_result.get("previewSubmit")),
                    history_store=history_store,
                )
                if _clean_optional_text(submit_result.get("status")) == "submitted"
                else None
            ),
            blocked_reason_codes=_string_list(submit_result.get("blockedReasonCodes")),
            error_type=_clean_optional_text(error.get("class") if error else None),
        )

    async def execute_manga_mask_assistant_inpaint_preview_submit_action(
        self,
        submit_generation: GenerationSubmitter | None,
        *,
        action_id: str = "sam3_manga_mask_assistant.inpaint_preview.submit",
        result_asset_collector: Callable[[Sequence[Mapping[str, object]]], None] | None = None,
    ) -> dict[str, Any]:
        self._last_manga_inpaint_preview_submit_result = None
        self._last_manga_inpaint_preview_history_metadata = None
        request = self._last_manga_inpaint_preview_request
        if request is None:
            payload = _manga_mask_assistant_inpaint_preview_submit_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=("manga_inpaint_preview_request_missing",),
            )
            self._emit_manga_inpaint_preview_submitted(payload)
            return payload
        if not callable(submit_generation):
            payload = _manga_mask_assistant_inpaint_preview_submit_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=("generation_submitter_missing",),
            )
            self._emit_manga_inpaint_preview_submitted(payload)
            return payload

        submit_result = await submit_manga_inpaint_preview_request(
            request,
            submit_generation,
            action_id=action_id,
            result_asset_collector=result_asset_collector,
        )
        status = _clean_optional_text(submit_result.get("status")) or "failed"
        handled = bool(submit_result.get("handled", False))
        blocked_reason_codes = _string_list(submit_result.get("blockedReasonCodes"))
        preview_summary = None
        if status == "submitted":
            self._last_manga_inpaint_preview_submit_result = dict(submit_result)
            preview_summary = _manga_mask_assistant_inpaint_preview_submit_summary(submit_result)

        error = _mapping(submit_result.get("error"))
        payload = _manga_mask_assistant_inpaint_preview_submit_action_result_payload(
            action_id,
            status=status,
            handled=handled,
            preview_submit=preview_summary,
            blocked_reason_codes=blocked_reason_codes,
            error_type=_clean_optional_text(error.get("class") if error else None),
        )
        self._emit_manga_inpaint_preview_submitted(payload)
        return payload

    def _emit_manga_inpaint_preview_submitted(self, payload: Mapping[str, Any]) -> None:
        """Emit the manga inpaint submit event for one terminal result.

        Called exactly once per terminal (submitted / failed / blocked):
        either by the submit action method above, or by the bound handler
        tail for bound-only terminals that never reach the method.
        """

        preview_summary = _mapping(payload.get("previewSubmit"))
        self._emit(
            "sam3_ai_select.manga_inpaint_preview_submitted",
            {
                "schemaVersion": _MANGA_INPAINT_PREVIEW_SUBMIT_ACTION_RESULT_SCHEMA_VERSION,
                "tool": TOOL_ID,
                "actionId": _clean_optional_text(payload.get("actionId")),
                "status": _clean_optional_text(payload.get("status")) or "failed",
                "handled": bool(payload.get("handled", False)),
                "previewSubmit": dict(preview_summary) if preview_summary else None,
                "blockedReasonCodes": _string_list(payload.get("blockedReasonCodes")),
                "errorType": _clean_optional_text(payload.get("errorType")),
            },
        )

    def execute_manga_mask_assistant_inpaint_preview_history_record_action(
        self,
        *,
        history_store: object | None = None,
        action_id: str = "sam3_manga_mask_assistant.inpaint_preview.history_record",
    ) -> dict[str, Any]:
        self._last_manga_inpaint_preview_history_metadata = None
        submit_result = self._last_manga_inpaint_preview_submit_result
        if submit_result is None:
            return _manga_mask_assistant_inpaint_preview_history_record_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                preview_history=_ai_effect_preview_submit_history_payload(
                    recorded=False,
                    status="blocked",
                    history_job_id=None,
                    blocked_reason_codes=("manga_inpaint_preview_submit_missing",),
                ),
                blocked_reason_codes=("manga_inpaint_preview_submit_missing",),
            )

        try:
            metadata = build_manga_inpaint_preview_history_metadata(submit_result)
        except Sam3MangaInpaintPreviewHistoryMetadataError as exc:
            return _manga_mask_assistant_inpaint_preview_history_record_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                preview_history=_ai_effect_preview_submit_history_payload(
                    recorded=False,
                    status="blocked",
                    history_job_id=None,
                    blocked_reason_codes=(exc.code,),
                ),
                blocked_reason_codes=(exc.code,),
                error_type=type(exc).__name__,
            )

        generation_history = self._resolve_generation_history_store(history_store)
        if generation_history is None:
            return _manga_mask_assistant_inpaint_preview_history_record_action_result_payload(
                action_id,
                status="skipped",
                handled=False,
                preview_history=_ai_effect_preview_submit_history_payload(
                    recorded=False,
                    status="skipped",
                    history_job_id=None,
                    blocked_reason_codes=("generation_history_store_missing",),
                ),
                blocked_reason_codes=("generation_history_store_missing",),
            )

        add_record = getattr(generation_history, "add_record", None)
        if not callable(add_record):
            return _manga_mask_assistant_inpaint_preview_history_record_action_result_payload(
                action_id,
                status="skipped",
                handled=False,
                preview_history=_ai_effect_preview_submit_history_payload(
                    recorded=False,
                    status="skipped",
                    history_job_id=None,
                    blocked_reason_codes=("generation_history_store_unavailable",),
                ),
                blocked_reason_codes=("generation_history_store_unavailable",),
            )

        record = _build_manga_inpaint_preview_submit_generation_record(
            settings=self.settings,
            metadata=metadata,
        )
        try:
            add_record(record)
        except Exception as exc:
            logger.debug("SAM3 manga inpaint preview history add failed", exc_info=True)
            return _manga_mask_assistant_inpaint_preview_history_record_action_result_payload(
                action_id,
                status="failed",
                handled=False,
                preview_history=_ai_effect_preview_submit_history_payload(
                    recorded=False,
                    status="failed",
                    history_job_id=None,
                    blocked_reason_codes=("manga_inpaint_preview_history_record_rejected",),
                ),
                blocked_reason_codes=("manga_inpaint_preview_history_record_rejected",),
                error_type=type(exc).__name__,
            )

        self._last_manga_inpaint_preview_history_metadata = dict(metadata)
        return _manga_mask_assistant_inpaint_preview_history_record_action_result_payload(
            action_id,
            status="recorded",
            handled=True,
            preview_history=_ai_effect_preview_submit_history_payload(
                recorded=True,
                status="recorded",
                history_job_id=record.job_id,
            ),
        )

    def execute_generation_result_diff_qa_action(
        self,
        action_id: str,
        *,
        diff_mask_asset_id: str,
        protected_mask_asset_id: str | None = None,
        canvas: object | None = None,
        asset_store: Sam3AssetStore | None = None,
        canvas_width: int | None = None,
        canvas_height: int | None = None,
    ) -> dict[str, Any]:
        resolved_store = asset_store or self._last_asset_store
        if resolved_store is None:
            return _generation_diff_qa_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_asset_store_missing"],
            )

        canvas_size = _positive_size(canvas_width, canvas_height)
        if canvas_size is None and canvas is not None:
            canvas_size = _canvas_size(canvas)
        if canvas_size is None:
            return _generation_diff_qa_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["invalid_canvas_size"],
            )

        try:
            qa = analyze_generation_result_diff_mask(
                resolved_store,
                diff_mask_asset_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                protected_mask_asset_id=protected_mask_asset_id,
            )
        except Sam3GenerationResultDiffQAError as exc:
            return _generation_diff_qa_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
                error_type=type(exc).__name__,
            )

        return _generation_diff_qa_action_result_payload(
            action_id,
            status="passed" if qa.ok else "warning",
            handled=True,
            generation_diff_qa=qa,
        )

    def execute_generation_result_diff_qa_history_action(
        self,
        action_id: str,
        *,
        history_job_id: str,
        diff_mask_asset_id: str,
        protected_mask_asset_id: str | None = None,
        canvas: object | None = None,
        asset_store: Sam3AssetStore | None = None,
        history_store: object | None = None,
        canvas_width: int | None = None,
        canvas_height: int | None = None,
    ) -> dict[str, Any]:
        target = self._resolve_generation_diff_qa_history_target(
            action_id,
            history_job_id=history_job_id,
            history_store=history_store,
        )
        if isinstance(target, dict):
            return target
        clean_history_job_id, update_record, record = target

        qa_result = self.execute_generation_result_diff_qa_action(
            action_id,
            diff_mask_asset_id=diff_mask_asset_id,
            protected_mask_asset_id=protected_mask_asset_id,
            canvas=canvas,
            asset_store=asset_store,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
        qa_summary = _mapping(qa_result.get("generationDiffQA"))
        qa_status = _clean_optional_text(qa_result.get("status")) or "blocked"
        if qa_status == "blocked" or qa_summary is None:
            blocked_reason_codes = _string_list(qa_result.get("blockedReasonCodes"))
            return _generation_diff_qa_history_blocked_result_payload(
                action_id,
                blocked_reason_codes,
                history_job_id=clean_history_job_id,
                generation_diff_qa=qa_summary,
                error_type=_clean_optional_text(qa_result.get("errorType")),
            )

        updated_record = _generation_record_with_generation_diff_qa(
            record,
            qa_summary,
            qa_status=qa_status,
        )
        try:
            update_accepted = bool(update_record(updated_record))
        except Exception:
            logger.debug("SAM3 generation-diff QA history update failed", exc_info=True)
            update_accepted = False
        if not update_accepted:
            return _generation_diff_qa_history_blocked_result_payload(
                action_id,
                ["generation_history_record_update_rejected"],
                history_job_id=clean_history_job_id,
                generation_diff_qa=qa_summary,
                history_status="failed",
            )

        return _generation_diff_qa_history_action_result_payload(
            action_id,
            status="updated" if bool(qa_summary.get("ok", False)) else "warning",
            handled=True,
            generation_diff_qa=qa_summary,
            history_update=_generation_diff_qa_history_update_payload(
                updated=True,
                status="updated",
                history_job_id=clean_history_job_id,
            ),
        )

    def execute_preview_apply_readiness_history_action(
        self,
        action_id: str,
        *,
        history_job_id: str,
        history_store: object | None = None,
        require_generation_diff_qa_ok: bool = True,
    ) -> dict[str, Any]:
        target = self._resolve_preview_apply_readiness_history_target(
            action_id,
            history_job_id=history_job_id,
            history_store=history_store,
        )
        if isinstance(target, dict):
            return target
        clean_history_job_id, record = target

        readiness = analyze_preview_apply_readiness(
            record,
            require_generation_diff_qa_ok=require_generation_diff_qa_ok,
        )
        blocked_reason_codes = list(readiness.blocked_reason_codes)
        return _preview_apply_readiness_history_action_result_payload(
            action_id,
            status="ready" if readiness.ready else "blocked",
            handled=readiness.ready,
            preview_apply_readiness=readiness.to_dict(),
            history_lookup=_preview_apply_readiness_history_lookup_payload(
                found=True,
                status="found",
                history_job_id=clean_history_job_id,
            ),
            blocked_reason_codes=blocked_reason_codes,
        )

    def execute_preview_output_image_path_history_action(  # noqa: PLR0911
        self,
        action_id: str,
        *,
        history_job_id: str,
        history_store: object | None = None,
    ) -> dict[str, Any]:
        target = self._resolve_preview_output_image_path_history_target(
            action_id,
            history_job_id=history_job_id,
            history_store=history_store,
        )
        if isinstance(target, dict):
            return target
        clean_history_job_id, generation_history, update_record, record = target

        readiness = analyze_preview_apply_readiness(
            record,
            require_generation_diff_qa_ok=False,
        )
        if readiness.blocked_reason_codes == ("sam3_preview_history_record_invalid",):
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["sam3_preview_history_record_invalid"],
                history_job_id=clean_history_job_id,
                history_lookup_status="found",
            )

        if _clean_optional_text(getattr(record, "image_path", None)) is not None:
            return _preview_output_image_path_history_action_result_payload(
                action_id,
                status="ready",
                handled=True,
                image_path_promotion=_preview_output_image_path_promotion_payload(
                    updated=False,
                    status="already_available",
                    history_job_id=clean_history_job_id,
                    image_path_available=True,
                    candidate_path_available=False,
                ),
                history_lookup=_preview_apply_readiness_history_lookup_payload(
                    found=True,
                    status="found",
                    history_job_id=clean_history_job_id,
                ),
            )

        candidate = _preview_output_image_path_candidate(record)
        if candidate is None:
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["preview_output_image_path_missing"],
                history_job_id=clean_history_job_id,
                history_lookup_status="found",
            )
        candidate_path, candidate_error = _safe_preview_output_image_path(candidate)
        if candidate_error is not None:
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                [candidate_error],
                history_job_id=clean_history_job_id,
                history_lookup_status="found",
                candidate_path_available=True,
            )
        if getattr(generation_history, "base_path", None) is None:
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
                history_lookup_status="found",
                candidate_path_available=True,
            )
        if candidate_path is None or not _preview_output_file_exists(
            generation_history,
            candidate_path,
        ):
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["preview_output_image_file_missing"],
                history_job_id=clean_history_job_id,
                history_lookup_status="found",
                candidate_path_available=candidate_path is not None,
            )

        updated_record = replace(record, image_path=candidate_path)
        try:
            update_accepted = bool(update_record(updated_record))
        except Exception:
            logger.debug("SAM3 preview output image path history update failed", exc_info=True)
            update_accepted = False
        if not update_accepted:
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["generation_history_record_update_rejected"],
                history_job_id=clean_history_job_id,
                history_lookup_status="found",
                candidate_path_available=True,
            )

        return _preview_output_image_path_history_action_result_payload(
            action_id,
            status="updated",
            handled=True,
            image_path_promotion=_preview_output_image_path_promotion_payload(
                updated=True,
                status="updated",
                history_job_id=clean_history_job_id,
                image_path_available=True,
                candidate_path_available=True,
            ),
            history_lookup=_preview_apply_readiness_history_lookup_payload(
                found=True,
                status="found",
                history_job_id=clean_history_job_id,
            ),
        )

    def _resolve_preview_output_image_path_history_target(
        self,
        action_id: str,
        *,
        history_job_id: str,
        history_store: object | None,
    ) -> tuple[str, object, Callable[[Any], object], GenerationRecord] | dict[str, Any]:
        clean_history_job_id = _clean_optional_text(history_job_id)
        if clean_history_job_id is None:
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["generation_history_job_id_missing"],
                history_job_id=None,
            )

        generation_history = self._resolve_generation_history_store(history_store)
        if generation_history is None:
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["generation_history_store_missing"],
                history_job_id=clean_history_job_id,
            )

        get_record = getattr(generation_history, "get_record", None)
        update_record = getattr(generation_history, "update_record", None)
        if not callable(get_record) or not callable(update_record):
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
            )

        try:
            record = get_record(clean_history_job_id)
        except Exception:
            logger.debug("SAM3 preview output image path history lookup failed", exc_info=True)
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
            )
        if record is None:
            return _preview_output_image_path_history_blocked_result_payload(
                action_id,
                ["generation_history_record_missing"],
                history_job_id=clean_history_job_id,
            )
        return (clean_history_job_id, generation_history, update_record, record)

    def execute_preview_apply_history_action(  # noqa: PLR0911
        self,
        action_id: str,
        *,
        history_job_id: str,
        canvas: object | None = None,
        history_store: object | None = None,
        target_layer_id: str | None = None,
        merge_key: str | None = None,
    ) -> dict[str, Any]:
        clean_target_layer_id = _clean_optional_text(target_layer_id)
        target = self._resolve_preview_apply_history_target(
            action_id,
            history_job_id=history_job_id,
            history_store=history_store,
            target_layer_id=clean_target_layer_id,
        )
        if isinstance(target, dict):
            return target
        clean_history_job_id, generation_history, record = target

        readiness = analyze_preview_apply_readiness(record)
        if not readiness.ready:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                readiness.blocked_reason_codes,
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                history_lookup_status="found",
                image_path_available=readiness.image_path_available,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
            )

        image_path, image_path_error = _safe_preview_output_image_path(
            str(getattr(record, "image_path", "") or "")
        )
        if image_path_error is not None:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                [image_path_error],
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                history_lookup_status="found",
                image_path_available=True,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
            )
        image_file = (
            _preview_output_existing_file_path(generation_history, image_path)
            if image_path is not None
            else None
        )
        if image_file is None:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["preview_output_image_file_missing"],
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                history_lookup_status="found",
                image_path_available=True,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
            )

        resolved_canvas = canvas or getattr(self.host_api, "canvas", None)
        if resolved_canvas is None:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["canvas_unavailable"],
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                history_lookup_status="found",
                image_path_available=True,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
            )
        submit = getattr(resolved_canvas, "submit", None)
        if not callable(submit):
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["canvas_command_submit_unavailable"],
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                history_lookup_status="found",
                image_path_available=True,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
            )

        image_array = _preview_apply_image_array(image_file)
        if image_array is None:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["preview_output_image_decode_failed"],
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                history_lookup_status="found",
                image_path_available=True,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
            )

        try:
            from ...core.commands import ApplyImageCommand  # noqa: PLC0415

            metadata = {
                "source": "sam3_ai_select",
                "history_job_id": clean_history_job_id,
            }
            if clean_target_layer_id is not None:
                metadata["target_layer_id"] = clean_target_layer_id
            submit_merge_key = merge_key or f"sam3-preview-apply:{clean_history_job_id}"
            command = ApplyImageCommand(
                image_array,
                metadata=metadata,
                merge_key=submit_merge_key,
            )
            applied = bool(submit(command, merge_key=submit_merge_key))
        except Exception as exc:
            logger.debug("SAM3 preview apply history action failed", exc_info=True)
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["canvas_apply_image_rejected"],
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                history_lookup_status="found",
                image_path_available=True,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
                error_type=type(exc).__name__,
            )
        if not applied:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["canvas_apply_image_rejected"],
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                history_lookup_status="found",
                image_path_available=True,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
            )

        return _preview_apply_history_action_result_payload(
            action_id,
            status="applied",
            handled=True,
            preview_apply=_preview_apply_payload(
                applied=True,
                status="applied",
                history_job_id=clean_history_job_id,
                target_layer_id=clean_target_layer_id,
                image_path_available=True,
                generation_diff_qa_ok=readiness.generation_diff_qa_ok,
            ),
            history_lookup=_preview_apply_readiness_history_lookup_payload(
                found=True,
                status="found",
                history_job_id=clean_history_job_id,
            ),
        )

    def _resolve_preview_apply_history_target(
        self,
        action_id: str,
        *,
        history_job_id: str,
        history_store: object | None,
        target_layer_id: str | None,
    ) -> tuple[str, object, GenerationRecord] | dict[str, Any]:
        clean_history_job_id = _clean_optional_text(history_job_id)
        if clean_history_job_id is None:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["generation_history_job_id_missing"],
                history_job_id=None,
                target_layer_id=target_layer_id,
            )

        generation_history = self._resolve_generation_history_store(history_store)
        if generation_history is None:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["generation_history_store_missing"],
                history_job_id=clean_history_job_id,
                target_layer_id=target_layer_id,
            )

        get_record = getattr(generation_history, "get_record", None)
        if not callable(get_record):
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
                target_layer_id=target_layer_id,
            )

        try:
            record = get_record(clean_history_job_id)
        except Exception:
            logger.debug("SAM3 preview apply history lookup failed", exc_info=True)
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
                target_layer_id=target_layer_id,
            )
        if record is None:
            return _preview_apply_history_blocked_result_payload(
                action_id,
                ["generation_history_record_missing"],
                history_job_id=clean_history_job_id,
                target_layer_id=target_layer_id,
            )
        return (clean_history_job_id, generation_history, record)

    def _resolve_preview_apply_readiness_history_target(
        self,
        action_id: str,
        *,
        history_job_id: str,
        history_store: object | None,
    ) -> tuple[str, GenerationRecord] | dict[str, Any]:
        clean_history_job_id = _clean_optional_text(history_job_id)
        if clean_history_job_id is None:
            return _preview_apply_readiness_history_blocked_result_payload(
                action_id,
                ["generation_history_job_id_missing"],
                history_job_id=None,
            )

        generation_history = self._resolve_generation_history_store(history_store)
        if generation_history is None:
            return _preview_apply_readiness_history_blocked_result_payload(
                action_id,
                ["generation_history_store_missing"],
                history_job_id=clean_history_job_id,
            )

        get_record = getattr(generation_history, "get_record", None)
        if not callable(get_record):
            return _preview_apply_readiness_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
            )

        try:
            record = get_record(clean_history_job_id)
        except Exception:
            logger.debug("SAM3 preview-apply readiness history lookup failed", exc_info=True)
            return _preview_apply_readiness_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
            )
        if record is None:
            return _preview_apply_readiness_history_blocked_result_payload(
                action_id,
                ["generation_history_record_missing"],
                history_job_id=clean_history_job_id,
            )
        return (clean_history_job_id, record)

    def _resolve_generation_diff_qa_history_target(
        self,
        action_id: str,
        *,
        history_job_id: str,
        history_store: object | None,
    ) -> tuple[str, Callable[[Any], object], GenerationRecord] | dict[str, Any]:
        clean_history_job_id = _clean_optional_text(history_job_id)
        if clean_history_job_id is None:
            return _generation_diff_qa_history_blocked_result_payload(
                action_id,
                ["generation_history_job_id_missing"],
                history_job_id=None,
            )

        generation_history = self._resolve_generation_history_store(history_store)
        if generation_history is None:
            return _generation_diff_qa_history_blocked_result_payload(
                action_id,
                ["generation_history_store_missing"],
                history_job_id=clean_history_job_id,
            )

        get_record = getattr(generation_history, "get_record", None)
        update_record = getattr(generation_history, "update_record", None)
        if not callable(get_record) or not callable(update_record):
            return _generation_diff_qa_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
            )

        try:
            record = get_record(clean_history_job_id)
        except Exception:
            logger.debug("SAM3 generation-diff QA history lookup failed", exc_info=True)
            return _generation_diff_qa_history_blocked_result_payload(
                action_id,
                ["generation_history_store_unavailable"],
                history_job_id=clean_history_job_id,
            )
        if record is None:
            return _generation_diff_qa_history_blocked_result_payload(
                action_id,
                ["generation_history_record_missing"],
                history_job_id=clean_history_job_id,
            )
        return (clean_history_job_id, update_record, record)

    def _record_ai_effect_preview_submit_history(
        self,
        preview_submit: Mapping[str, Any] | None,
        *,
        history_store: object | None,
    ) -> dict[str, Any] | None:
        if preview_submit is None:
            return _ai_effect_preview_submit_history_payload(
                recorded=False,
                status="skipped",
                history_job_id=None,
                blocked_reason_codes=["preview_submit_missing"],
            )

        generation_history = self._resolve_generation_history_store(history_store)
        if generation_history is None:
            return _ai_effect_preview_submit_history_payload(
                recorded=False,
                status="skipped",
                history_job_id=None,
                blocked_reason_codes=["generation_history_store_missing"],
            )

        add_record = getattr(generation_history, "add_record", None)
        if not callable(add_record):
            return _ai_effect_preview_submit_history_payload(
                recorded=False,
                status="skipped",
                history_job_id=None,
                blocked_reason_codes=["generation_history_store_unavailable"],
            )

        record = _build_ai_effect_preview_submit_generation_record(
            settings=self.settings,
            preview_submit=preview_submit,
        )
        try:
            add_record(record)
        except Exception:
            logger.debug("SAM3 preview-submit history add failed", exc_info=True)
            return _ai_effect_preview_submit_history_payload(
                recorded=False,
                status="failed",
                history_job_id=None,
                blocked_reason_codes=["preview_generation_history_record_rejected"],
            )

        return _ai_effect_preview_submit_history_payload(
            recorded=True,
            status="recorded",
            history_job_id=record.job_id,
        )

    def execute_context_action(  # noqa: PLR0911, PLR0912 - flat action dispatcher
        self,
        action_id: str,
        *,
        canvas: Sam3SelectionCanvasSubmitter | None = None,
        source: str | None = "sam3.selection.apply",
        merge_key: str | None = None,
        asset_store: Sam3AssetStore | None = None,
        history_store: object | None = None,
        selected_candidate_ids: Iterable[str] | None = None,
        combine_mode: object | None = None,
        mask_refine_options: Mapping[str, object] | None = None,
        effect_layer_id: str | None = None,
        slot_id: str | None = None,
        exclude_blend: bool = True,
        force_regenerate: bool = False,
        preview_param_values: Mapping[str, object] | None = None,
        preview_slot_refs: Mapping[str, object] | None = None,
        preview_param_ui_state: Mapping[str, object] | None = None,
        preview_replace_param_values: bool = False,
        preview_replace_slot_refs: bool = False,
        preview_replace_param_ui_state: bool = False,
        preview_trigger: str | None = None,
        preview_target_server_id: str | None = None,
        preview_output_mode_override: str | None = None,
        preview_metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        store = asset_store if asset_store is not None else self._last_asset_store
        generation_history = self._resolve_generation_history_store(history_store)
        snapshot = _context_actions_snapshot_payload(
            self._last_session,
            self._last_overlay_model,
            store,
            generation_history,
            self._last_run_status,
            self.settings,
            candidate_overlay_visible=self._candidate_overlay_visible,
        )
        action = _context_action_by_id(snapshot, action_id)
        if action is None:
            return _context_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        if (
            action_id.startswith(_CONTEXT_OPERATION_ACTION_PREFIX)
            or action_id.startswith(_CONTEXT_CANDIDATE_SELECT_ACTION_PREFIX)
        ) and action.get("checked"):
            return _context_action_result_payload(
                action_id,
                status="noop",
                handled=True,
                session=self._last_session,
            )

        if not action.get("enabled", False):
            return self._disabled_context_action_result(action_id, action)

        async_blocked_reason_codes = _async_context_blocked_reason_codes(action_id)
        if async_blocked_reason_codes:
            return _context_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=async_blocked_reason_codes,
            )
        if _is_preview_submit_context_action_id(action_id):
            # Preview actions escalate to the async executor only when the
            # host exposes a generation submitter; otherwise they keep the
            # legacy synchronous prepare-only behavior.
            if self._preview_submit_in_flight:
                return _context_action_result_payload(
                    action_id,
                    status="blocked",
                    handled=False,
                    blocked_reason_codes=["ai_select_preview_submit_in_flight"],
                )
            if self._resolve_generation_submitter() is not None:
                return _context_action_result_payload(
                    action_id,
                    status="blocked",
                    handled=False,
                    blocked_reason_codes=["ai_select_preview_submit_requires_async_executor"],
                )

        if action_id == _CONTEXT_ACTION_COMBINE_SELECTED:
            return self._execute_context_combine_selected_action(
                action_id,
                canvas=canvas,
                asset_store=store,
                selected_candidate_ids=selected_candidate_ids,
                combine_mode=combine_mode,
            )
        if action_id == _CONTEXT_ACTION_MASK_REFINE_SELECTED:
            return self._execute_context_mask_refine_action(
                action_id,
                canvas=canvas,
                asset_store=store,
                mask_refine_options=mask_refine_options,
            )
        if action_id == _CONTEXT_ACTION_GROUP_SELECTED_TO_LAYER_GROUP:
            return self._execute_context_candidate_group_to_layer_group_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=store,
                selected_candidate_ids=selected_candidate_ids,
            )
        if action_id == _CONTEXT_ACTION_TOGGLE_CANDIDATE_OVERLAY:
            return self._execute_context_toggle_candidate_overlay_action(action_id)
        if action_id == _CONTEXT_ACTION_USE_CURRENT_AS_INPAINT_MASK:
            return self._execute_context_ai_effect_mask_slot_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                exclude_blend=exclude_blend,
            )
        if action_id == _CONTEXT_ACTION_USE_CURRENT_AS_BACKGROUND_GENERATION_MASK:
            return self._execute_context_ai_effect_mask_slot_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                exclude_blend=exclude_blend,
                background_generation=True,
            )
        if action_id == _CONTEXT_ACTION_USE_SELECTION_AS_INPAINT_MASK:
            return self._execute_context_selection_inpaint_mask_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                exclude_blend=exclude_blend,
            )
        if action_id in _CONTEXT_EXPORT_ACTION_IDS:
            return self._execute_context_asset_export_action(
                action_id,
                canvas=canvas,
                asset_store=store,
            )
        if action_id == _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT:
            return self._execute_context_selection_preview_request_action(
                action_id,
                canvas=canvas,
                asset_store=store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                force_regenerate=force_regenerate,
                preview_param_values=preview_param_values,
                preview_slot_refs=preview_slot_refs,
                preview_param_ui_state=preview_param_ui_state,
                preview_replace_param_values=preview_replace_param_values,
                preview_replace_slot_refs=preview_replace_slot_refs,
                preview_replace_param_ui_state=preview_replace_param_ui_state,
                preview_trigger=preview_trigger,
                preview_target_server_id=preview_target_server_id,
                preview_output_mode_override=preview_output_mode_override,
                preview_metadata=preview_metadata,
            )
        if action_id == _CONTEXT_ACTION_PREVIEW_CURRENT_INPAINT:
            return self._execute_context_ai_effect_preview_request_action(
                action_id,
                canvas=canvas,
                asset_store=store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                force_regenerate=force_regenerate,
                preview_param_values=preview_param_values,
                preview_slot_refs=preview_slot_refs,
                preview_param_ui_state=preview_param_ui_state,
                preview_replace_param_values=preview_replace_param_values,
                preview_replace_slot_refs=preview_replace_slot_refs,
                preview_replace_param_ui_state=preview_replace_param_ui_state,
                preview_trigger=preview_trigger,
                preview_target_server_id=preview_target_server_id,
                preview_output_mode_override=preview_output_mode_override,
                preview_metadata=preview_metadata,
            )
        if action_id.startswith(_CONTEXT_CANDIDATE_PREVIEW_INPAINT_ACTION_PREFIX):
            return self._execute_context_candidate_ai_effect_preview_request_action(
                action_id,
                canvas=canvas,
                asset_store=store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                force_regenerate=force_regenerate,
                preview_param_values=preview_param_values,
                preview_slot_refs=preview_slot_refs,
                preview_param_ui_state=preview_param_ui_state,
                preview_replace_param_values=preview_replace_param_values,
                preview_replace_slot_refs=preview_replace_slot_refs,
                preview_replace_param_ui_state=preview_replace_param_ui_state,
                preview_trigger=preview_trigger,
                preview_target_server_id=preview_target_server_id,
                preview_output_mode_override=preview_output_mode_override,
                preview_metadata=preview_metadata,
            )
        if action_id.startswith(_CONTEXT_CANDIDATE_USE_INPAINT_MASK_ACTION_PREFIX):
            return self._execute_context_candidate_ai_effect_mask_slot_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                exclude_blend=exclude_blend,
            )
        if action_id.startswith(_CONTEXT_CANDIDATE_USE_BACKGROUND_GENERATION_MASK_ACTION_PREFIX):
            return self._execute_context_candidate_ai_effect_mask_slot_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
                exclude_blend=exclude_blend,
                background_generation=True,
            )

        if action_id in {
            _CONTEXT_ACTION_APPLY_CURRENT,
            *_CONTEXT_APPLY_OPERATION_BY_ACTION_ID,
            _CONTEXT_ACTION_APPLY_CURRENT_AS_LAYER_MASK,
            _CONTEXT_ACTION_ADD_CURRENT_TO_LAYER_MASK,
            _CONTEXT_ACTION_APPLY_CURRENT_AS_CLIPPING_MASK,
            _CONTEXT_ACTION_APPLY_CURRENT_TO_NEW_LAYER,
            *_CONTEXT_ACTION_EXTRACT_TO_LAYER_IDS,
            _CONTEXT_ACTION_SAVE_CURRENT_MASK_ASSET,
        } or action_id.startswith(
            (
                _CONTEXT_CANDIDATE_APPLY_LAYER_MASK_ACTION_PREFIX,
                _CONTEXT_CANDIDATE_APPLY_CLIPPING_MASK_ACTION_PREFIX,
                _CONTEXT_CANDIDATE_CUT_TO_LAYER_ACTION_PREFIX,
                _CONTEXT_CANDIDATE_EXTRACT_FOREGROUND_ACTION_PREFIX,
                _CONTEXT_CANDIDATE_EXTRACT_BACKGROUND_ACTION_PREFIX,
                _CONTEXT_CANDIDATE_SAVE_MASK_ASSET_ACTION_PREFIX,
            )
        ):
            return self._execute_context_apply_action_by_id(
                action_id,
                canvas=canvas,
                source=source,
                merge_key=merge_key,
                asset_store=store,
                history_store=generation_history,
            )
        return self._execute_context_session_action(action_id, action)

    def _execute_context_toggle_candidate_overlay_action(self, action_id: str) -> dict[str, Any]:
        self._candidate_overlay_visible = not self._candidate_overlay_visible
        self._sync_overlay_hook()
        return _context_action_result_payload(
            action_id,
            status="visible" if self._candidate_overlay_visible else "hidden",
            handled=True,
            session=self._last_session,
        )

    def _disabled_context_action_result(
        self,
        action_id: str,
        action: Mapping[str, Any],
    ) -> dict[str, Any]:
        blocked_reason_codes = action.get("blockedReasonCodes", [])
        specialized_result = _disabled_apply_action_result_payload(
            action_id,
            blocked_reason_codes,
        )
        if specialized_result is not None:
            return specialized_result
        return _context_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )

    def _execute_context_apply_action_by_id(
        self,
        action_id: str,
        *,
        canvas: Sam3SelectionCanvasSubmitter | None,
        source: str | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
        history_store: object | None,
    ) -> dict[str, Any]:
        if action_id == _CONTEXT_ACTION_APPLY_CURRENT or (
            action_id in _CONTEXT_APPLY_OPERATION_BY_ACTION_ID
        ):
            return self._execute_context_apply_action(
                action_id,
                canvas=canvas,
                source=source,
                merge_key=merge_key,
                asset_store=asset_store,
                operation=_CONTEXT_APPLY_OPERATION_BY_ACTION_ID.get(action_id),
            )
        if action_id.startswith(_CONTEXT_CANDIDATE_APPLY_LAYER_MASK_ACTION_PREFIX):
            return self._execute_context_candidate_layer_mask_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        if action_id == _CONTEXT_ACTION_ADD_CURRENT_TO_LAYER_MASK:
            return self._execute_context_add_to_layer_mask_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        if action_id == _CONTEXT_ACTION_APPLY_CURRENT_AS_CLIPPING_MASK:
            return self._execute_context_clipping_mask_apply_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        if action_id == _CONTEXT_ACTION_APPLY_CURRENT_TO_NEW_LAYER:
            result = self._execute_context_cut_to_layer_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        elif action_id in _CONTEXT_ACTION_EXTRACT_TO_LAYER_IDS:
            result = self._execute_context_extract_to_layer_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        elif action_id == _CONTEXT_ACTION_SAVE_CURRENT_MASK_ASSET:
            result = self._execute_context_save_mask_asset_action(
                action_id,
                asset_store=asset_store,
                history_store=history_store,
            )
        elif action_id.startswith(_CONTEXT_CANDIDATE_SAVE_MASK_ASSET_ACTION_PREFIX):
            result = self._execute_context_candidate_save_mask_asset_action(
                action_id,
                asset_store=asset_store,
                history_store=history_store,
            )
        elif action_id.startswith(
            (
                _CONTEXT_CANDIDATE_EXTRACT_FOREGROUND_ACTION_PREFIX,
                _CONTEXT_CANDIDATE_EXTRACT_BACKGROUND_ACTION_PREFIX,
            )
        ):
            result = self._execute_context_candidate_extract_to_layer_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        elif action_id.startswith(_CONTEXT_CANDIDATE_CUT_TO_LAYER_ACTION_PREFIX):
            result = self._execute_context_candidate_cut_to_layer_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        elif action_id.startswith(_CONTEXT_CANDIDATE_APPLY_CLIPPING_MASK_ACTION_PREFIX):
            result = self._execute_context_candidate_clipping_mask_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        else:
            result = self._execute_context_layer_mask_apply_action(
                action_id,
                canvas=canvas,
                merge_key=merge_key,
                asset_store=asset_store,
            )
        return result

    def _execute_context_apply_action(
        self,
        action_id: str,
        *,
        canvas: Sam3SelectionCanvasSubmitter | None,
        source: str | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        if canvas is None:
            return _context_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )
        apply_result = self.apply_current_candidate(
            canvas,
            source=source,
            merge_key=merge_key,
            asset_store=asset_store,
            operation=operation,
        )
        return _context_action_result_payload(
            action_id,
            status="applied" if apply_result.applied else "blocked",
            handled=True,
            apply_result=apply_result,
            blocked_reason_codes=[reason.code for reason in apply_result.blocked_reasons],
        )

    def _execute_context_layer_mask_apply_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        try:
            store, session, candidate, mask_asset_id, target_layer_id, canvas_size = (
                self._require_layer_mask_apply_context(
                    canvas=canvas,
                    asset_store=asset_store,
                )
            )
        except Sam3AISelectToolStateError as exc:
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            command = build_layer_mask_apply_command(
                store,
                mask_asset_id,
                layer_id=target_layer_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
            )
        except Sam3LayerMaskCommandAdapterError as exc:
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )

        try:
            submitted = bool(submit(command, merge_key=merge_key))
        except Exception:
            logger.debug("SAM3 layer mask command submit failed", exc_info=True)
            submitted = False

        if not submitted:
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                submitted=False,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                target_layer_id=target_layer_id,
                blocked_reason_codes=["layer_mask_apply_rejected"],
            )

        payload = _layer_mask_apply_payload(
            applied=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            target_layer_id=target_layer_id,
        )
        self._emit("sam3_ai_select.layer_mask_applied", payload)
        return _layer_mask_action_result_payload(
            action_id,
            status="applied",
            handled=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            target_layer_id=target_layer_id,
        )

    def _execute_context_add_to_layer_mask_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        try:
            store, session, candidate, mask_asset_id, target_layer_id, canvas_size = (
                self._require_layer_mask_apply_context(
                    canvas=canvas,
                    asset_store=asset_store,
                )
            )
            assert canvas is not None
            base_mask = _canvas_layer_mask(canvas, target_layer_id)
            if base_mask is None:
                raise Sam3AISelectToolStateError(
                    "target_layer_mask_missing",
                    "SAM3 layer mask add requires an existing target layer mask.",
                )
        except Sam3AISelectToolStateError as exc:
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            command = build_layer_mask_add_candidate_command(
                store,
                mask_asset_id,
                layer_id=target_layer_id,
                base_mask=base_mask,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
            )
        except Sam3LayerMaskCommandAdapterError as exc:
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )

        try:
            submitted = bool(submit(command, merge_key=merge_key))
        except Exception:
            logger.debug("SAM3 add-to-layer-mask command submit failed", exc_info=True)
            submitted = False

        if not submitted:
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                submitted=False,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                target_layer_id=target_layer_id,
                blocked_reason_codes=["layer_mask_add_rejected"],
            )

        payload = _layer_mask_apply_payload(
            applied=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            target_layer_id=target_layer_id,
        )
        self._emit("sam3_ai_select.layer_mask_applied", payload)
        return _layer_mask_action_result_payload(
            action_id,
            status="applied",
            handled=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            target_layer_id=target_layer_id,
        )

    def _execute_context_ai_effect_mask_slot_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
        exclude_blend: bool,
        background_generation: bool = False,
    ) -> dict[str, Any]:
        try:
            (
                store,
                session,
                candidate,
                mask_asset_id,
                target_effect_layer_id,
                target_slot_id,
                canvas_size,
            ) = self._require_ai_effect_mask_slot_context(
                canvas=canvas,
                asset_store=asset_store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
            )
        except Sam3AISelectToolStateError as exc:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        generation_purpose: str | None = None
        if background_generation:
            # Protect subject: invert the candidate's subject mask into a
            # deterministic background-generation mask asset (idempotent
            # atomic overwrite on repeated runs for the same candidate).
            try:
                background_asset = create_background_generation_mask_asset(
                    store,
                    subject_mask_asset_id=mask_asset_id,
                    output_asset_id=(
                        f"sam3-bg-genmask-{_sam3_asset_id_token(candidate.candidate_id)}"
                    ),
                    canvas_width=canvas_size[0],
                    canvas_height=canvas_size[1],
                    source_candidate_id=candidate.candidate_id,
                )
            except Sam3GenerationMaskPrepareError as exc:
                return _ai_effect_mask_slot_action_result_payload(
                    action_id,
                    status="blocked",
                    handled=False,
                    blocked_reason_codes=[exc.code],
                )
            mask_asset_id = background_asset.asset_id
            generation_purpose = "background"

        try:
            command = build_ai_effect_mask_slot_binding_command(
                store,
                mask_asset_id,
                effect_layer_id=target_effect_layer_id,
                slot_id=target_slot_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                mask_layer_name=(
                    "SAM3 Background Mask" if background_generation else "SAM3 Inpaint Mask"
                ),
                exclude_blend=exclude_blend,
            )
        except Sam3AIEffectSlotBindingError as exc:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )

        try:
            submitted = bool(submit(command, merge_key=merge_key))
        except Exception:
            logger.debug("SAM3 AI Effect mask slot command submit failed", exc_info=True)
            submitted = False

        mask_layer_id = _clean_optional_text(getattr(command, "mask_layer_id", None))
        if not submitted:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                submitted=False,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                effect_layer_id=target_effect_layer_id,
                slot_id=target_slot_id,
                mask_layer_id=mask_layer_id,
                exclude_blend=exclude_blend,
                blocked_reason_codes=["ai_effect_mask_slot_bind_rejected"],
            )

        payload = _ai_effect_mask_slot_payload(
            bound=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            effect_layer_id=target_effect_layer_id,
            slot_id=target_slot_id,
            mask_layer_id=mask_layer_id,
            exclude_blend=exclude_blend,
            generation_purpose=generation_purpose,
        )
        self._emit("sam3_ai_select.ai_effect_mask_slot_bound", payload)
        return _ai_effect_mask_slot_action_result_payload(
            action_id,
            status="bound",
            handled=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            effect_layer_id=target_effect_layer_id,
            slot_id=target_slot_id,
            mask_layer_id=mask_layer_id,
            exclude_blend=exclude_blend,
            generation_purpose=generation_purpose,
        )

    def _execute_context_selection_inpaint_mask_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
        exclude_blend: bool,
    ) -> dict[str, Any]:
        try:
            (
                store,
                selection_array,
                selection_bounds,
                target_effect_layer_id,
                target_slot_id,
                canvas_size,
            ) = self._require_selection_inpaint_mask_context(
                canvas=canvas,
                asset_store=asset_store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
            )
        except Sam3AISelectToolStateError as exc:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            selection_asset = create_selection_inpaint_mask_asset(
                store,
                selection_array,
                bounds=selection_bounds,
            )
        except Sam3GenerationMaskPrepareError as exc:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            command = build_ai_effect_mask_slot_binding_command(
                store,
                selection_asset.asset_id,
                effect_layer_id=target_effect_layer_id,
                slot_id=target_slot_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                mask_layer_name="SAM3 Selection Inpaint Mask",
                exclude_blend=exclude_blend,
            )
        except Sam3AIEffectSlotBindingError as exc:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )
        try:
            submitted = bool(submit(command, merge_key=merge_key))
        except Exception:
            logger.debug("SAM3 selection inpaint mask command submit failed", exc_info=True)
            submitted = False

        mask_layer_id = _clean_optional_text(getattr(command, "mask_layer_id", None))
        if not submitted:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                submitted=False,
                effect_layer_id=target_effect_layer_id,
                slot_id=target_slot_id,
                mask_layer_id=mask_layer_id,
                exclude_blend=exclude_blend,
                blocked_reason_codes=["ai_effect_mask_slot_bind_rejected"],
            )

        # Paired record: the selection preview verifies the current slot ref
        # still points at this layer before submitting.
        self._last_selection_inpaint_mask_asset_id = selection_asset.asset_id
        self._last_selection_inpaint_mask_layer_id = mask_layer_id

        payload = _ai_effect_mask_slot_payload(
            bound=True,
            submitted=True,
            candidate_set_id=None,
            candidate_id=None,
            effect_layer_id=target_effect_layer_id,
            slot_id=target_slot_id,
            mask_layer_id=mask_layer_id,
            exclude_blend=exclude_blend,
        )
        self._emit("sam3_ai_select.selection_inpaint_mask_bound", payload)
        return _ai_effect_mask_slot_action_result_payload(
            action_id,
            status="bound",
            handled=True,
            submitted=True,
            effect_layer_id=target_effect_layer_id,
            slot_id=target_slot_id,
            mask_layer_id=mask_layer_id,
            exclude_blend=exclude_blend,
        )

    def _execute_context_selection_preview_request_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
        force_regenerate: bool,
        preview_param_values: Mapping[str, object] | None,
        preview_slot_refs: Mapping[str, object] | None,
        preview_param_ui_state: Mapping[str, object] | None,
        preview_replace_param_values: bool,
        preview_replace_slot_refs: bool,
        preview_replace_param_ui_state: bool,
        preview_trigger: str | None,
        preview_target_server_id: str | None,
        preview_output_mode_override: str | None,
        preview_metadata: Mapping[str, object] | None,
    ) -> dict[str, Any]:
        try:
            (
                store,
                selection_asset_id,
                target_effect_layer_id,
                target_slot_id,
                canvas_size,
            ) = self._require_selection_preview_request_context(
                canvas=canvas,
                asset_store=asset_store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
            )
        except Sam3AISelectToolStateError as exc:
            return _ai_effect_preview_request_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            qa = analyze_generation_mask_asset(
                store,
                selection_asset_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
            )
            # Candidate ids stay None and the selection asset id is never
            # handed to the builder: the request payload must not carry
            # mask asset ids.
            request = build_ai_effect_preview_generation_request(
                target_effect_layer_id,
                candidate_set_id=None,
                candidate_id=None,
                qa=qa,
                force_regenerate=force_regenerate,
                param_values=preview_param_values,
                slot_refs=preview_slot_refs,
                param_ui_state=preview_param_ui_state,
                replace_param_values=preview_replace_param_values,
                replace_slot_refs=preview_replace_slot_refs,
                replace_param_ui_state=preview_replace_param_ui_state,
                trigger=preview_trigger,
                target_server_id=preview_target_server_id,
                output_mode_override=preview_output_mode_override,
                metadata=preview_metadata,
            )
        except (Sam3GenerationMaskQAError, Sam3AIEffectPreviewRequestError) as exc:
            return _ai_effect_preview_request_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return _ai_effect_preview_request_action_result_payload(
            action_id,
            status="prepared",
            handled=True,
            effect_layer_id=target_effect_layer_id,
            slot_id=target_slot_id,
            request=request,
        )

    def _resolve_selection_asset_store(
        self,
        asset_store: Sam3AssetStore | None,
    ) -> Sam3AssetStore:
        """Resolve the store for selection actions without requiring a run.

        Falls back to the default store root (the same one provider bindings
        use) because ``clear_run_state`` drops ``_last_asset_store`` on every
        new run.
        """

        if asset_store is not None:
            return asset_store
        if self._last_asset_store is not None:
            return self._last_asset_store
        try:
            return Sam3AssetStore()
        except OSError as exc:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            ) from exc

    def _require_selection_inpaint_mask_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
    ) -> tuple[Sam3AssetStore, Any, tuple[int, int, int, int], str, str, tuple[int, int]]:
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 selection inpaint mask binding requires a canvas.",
            )
        store = self._resolve_selection_asset_store(asset_store)

        get_selection_mask = getattr(canvas, "get_selection_mask", None)
        get_selection_bounds = getattr(canvas, "get_selection_bounds", None)
        selection_mask = get_selection_mask() if callable(get_selection_mask) else None
        selection_rect = get_selection_bounds() if callable(get_selection_bounds) else None
        if selection_mask is None or selection_rect is None or selection_rect.isEmpty():
            raise Sam3AISelectToolStateError(
                "selection_missing",
                "SAM3 selection inpaint mask binding requires an active selection.",
            )
        selection_bounds = (
            int(selection_rect.x()),
            int(selection_rect.y()),
            int(selection_rect.width()),
            int(selection_rect.height()),
        )
        selection_array = _selection_mask_to_array(selection_mask)

        target_layer_id, target_slot_id, canvas_size = self._require_ai_effect_target(
            canvas,
            effect_layer_id=effect_layer_id,
            slot_id=slot_id,
        )
        return (
            store,
            selection_array,
            selection_bounds,
            target_layer_id,
            target_slot_id,
            canvas_size,
        )

    def _require_selection_preview_request_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
    ) -> tuple[Sam3AssetStore, str, str, str, tuple[int, int]]:
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 selection preview request requires a canvas.",
            )
        store = self._resolve_selection_asset_store(asset_store)

        selection_asset_id = _clean_optional_text(self._last_selection_inpaint_mask_asset_id)
        bound_layer_id = _clean_optional_text(self._last_selection_inpaint_mask_layer_id)
        if selection_asset_id is None or bound_layer_id is None:
            raise Sam3AISelectToolStateError(
                "target_ai_effect_mask_slot_missing",
                "SAM3 selection preview requires a bound selection inpaint mask.",
            )

        target_layer_id, target_slot_id, canvas_size = self._require_ai_effect_target(
            canvas,
            effect_layer_id=effect_layer_id,
            slot_id=slot_id,
        )
        # The slot ref must still point at the mask layer this tool bound
        # from the selection; a candidate re-bind or an undo swaps it out and
        # the paired record no longer matches.
        target_layer = _canvas_layer_by_id(canvas, target_layer_id)
        state = getattr(target_layer, "ai_effect", None)
        slot_refs = getattr(state, "slot_refs", None)
        slot_ref = slot_refs.get(target_slot_id) if isinstance(slot_refs, Mapping) else None
        slot_layer_id = _clean_optional_text(getattr(slot_ref, "layer_id", None))
        if slot_ref is None or slot_layer_id != bound_layer_id:
            raise Sam3AISelectToolStateError(
                "target_ai_effect_mask_slot_missing",
                "SAM3 selection preview requires the bound selection mask slot.",
            )
        return (store, selection_asset_id, target_layer_id, target_slot_id, canvas_size)

    def _require_ai_effect_target(
        self,
        canvas: object,
        *,
        effect_layer_id: str | None,
        slot_id: str | None,
    ) -> tuple[str, str, tuple[int, int]]:
        target_layer_id = (
            _canvas_active_layer_id(canvas)
            if effect_layer_id is None
            else _clean_optional_text(effect_layer_id)
        )
        if target_layer_id is None:
            raise Sam3AISelectToolStateError(
                "target_ai_effect_layer_missing",
                "SAM3 AI Effect mask slot binding requires a target AI Effect layer.",
            )
        target_layer = _canvas_layer_by_id(canvas, target_layer_id)
        if (
            target_layer is None
            or getattr(target_layer, "layer_type", None) != "ai_effect"
            or getattr(target_layer, "ai_effect", None) is None
        ):
            raise Sam3AISelectToolStateError(
                "target_ai_effect_layer_missing",
                "SAM3 AI Effect mask slot binding target is not an AI Effect layer.",
            )
        target_slot_id = "mask" if slot_id is None else _clean_optional_text(slot_id)
        if target_slot_id is None:
            raise Sam3AISelectToolStateError(
                "target_slot_missing",
                "SAM3 AI Effect mask slot binding requires a slot id.",
            )
        canvas_size = _canvas_size(canvas)
        if canvas_size is None:
            raise Sam3AISelectToolStateError(
                "invalid_canvas_size",
                "SAM3 AI Effect mask slot binding requires a valid canvas size.",
            )
        return (target_layer_id, target_slot_id, canvas_size)

    def _execute_context_ai_effect_preview_request_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
        force_regenerate: bool,
        preview_param_values: Mapping[str, object] | None,
        preview_slot_refs: Mapping[str, object] | None,
        preview_param_ui_state: Mapping[str, object] | None,
        preview_replace_param_values: bool,
        preview_replace_slot_refs: bool,
        preview_replace_param_ui_state: bool,
        preview_trigger: str | None,
        preview_target_server_id: str | None,
        preview_output_mode_override: str | None,
        preview_metadata: Mapping[str, object] | None,
    ) -> dict[str, Any]:
        try:
            (
                store,
                session,
                candidate,
                mask_asset_id,
                target_effect_layer_id,
                target_slot_id,
                canvas_size,
            ) = self._require_ai_effect_preview_request_context(
                canvas=canvas,
                asset_store=asset_store,
                effect_layer_id=effect_layer_id,
                slot_id=slot_id,
            )
        except Sam3AISelectToolStateError as exc:
            return _ai_effect_preview_request_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            qa = analyze_generation_mask_asset(
                store,
                mask_asset_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
            )
            request = build_ai_effect_preview_generation_request(
                target_effect_layer_id,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                qa=qa,
                force_regenerate=force_regenerate,
                param_values=preview_param_values,
                slot_refs=preview_slot_refs,
                param_ui_state=preview_param_ui_state,
                replace_param_values=preview_replace_param_values,
                replace_slot_refs=preview_replace_slot_refs,
                replace_param_ui_state=preview_replace_param_ui_state,
                trigger=preview_trigger,
                target_server_id=preview_target_server_id,
                output_mode_override=preview_output_mode_override,
                metadata=preview_metadata,
            )
        except (Sam3GenerationMaskQAError, Sam3AIEffectPreviewRequestError) as exc:
            return _ai_effect_preview_request_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return _ai_effect_preview_request_action_result_payload(
            action_id,
            status="prepared",
            handled=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            effect_layer_id=target_effect_layer_id,
            slot_id=target_slot_id,
            request=request,
        )

    def _execute_context_clipping_mask_apply_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        try:
            store, session, candidate, mask_asset_id, target_layer_id, canvas_size = (
                self._require_layer_mask_apply_context(
                    canvas=canvas,
                    asset_store=asset_store,
                )
            )
        except Sam3AISelectToolStateError as exc:
            return _clipping_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            command = build_clipping_mask_apply_command(
                store,
                mask_asset_id,
                layer_id=target_layer_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
            )
        except Sam3LayerMaskCommandAdapterError as exc:
            return _clipping_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return _clipping_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )

        try:
            submitted = bool(submit(command, merge_key=merge_key))
        except Exception:
            logger.debug("SAM3 clipping mask command submit failed", exc_info=True)
            submitted = False

        if not submitted:
            return _clipping_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                submitted=False,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                target_layer_id=target_layer_id,
                blocked_reason_codes=["clipping_mask_apply_rejected"],
            )

        payload = _clipping_mask_apply_payload(
            applied=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            target_layer_id=target_layer_id,
        )
        self._emit("sam3_ai_select.clipping_mask_applied", payload)
        return _clipping_mask_action_result_payload(
            action_id,
            status="applied",
            handled=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            target_layer_id=target_layer_id,
        )

    def _execute_context_asset_export_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        if action_id == _CONTEXT_ACTION_EXPORT_SELECTION:
            return self._execute_context_export_selection_action(action_id, canvas=canvas)
        if action_id == _CONTEXT_ACTION_EXPORT_CURRENT_CANDIDATE:
            return self._execute_context_export_candidate_action(
                action_id, canvas=canvas, asset_store=asset_store
            )
        if action_id == _CONTEXT_ACTION_EXPORT_SPRITE_SHEET:
            return self._execute_context_export_sprite_sheet_action(
                action_id, canvas=canvas, asset_store=asset_store
            )
        return self._execute_context_export_all_candidates_action(
            action_id, canvas=canvas, asset_store=asset_store
        )

    def _execute_context_export_candidate_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        scope = "candidate"
        try:
            store, _session, _candidate, mask_asset_id, source_layer_id, canvas_size = (
                self._require_layer_mask_apply_context(canvas=canvas, asset_store=asset_store)
            )
            source_layer = self._resolve_export_source(
                canvas, canvas_size=canvas_size, active_layer_id=source_layer_id
            )
            png_bytes = self._build_candidate_export_png(
                store,
                mask_asset_id,
                source_layer=source_layer,
                canvas_size=canvas_size,
            )
        except Sam3AISelectToolStateError as exc:
            return self._blocked_asset_export_result(action_id, scope=scope, code=exc.code)

        # Everything is rendered before the modal dialog: a session change
        # while the dialog is open cannot invalidate the export.
        target_path = _prompt_asset_export_save_path("asset.png")
        if not target_path:
            return _asset_export_action_result_payload(
                action_id, status="cancelled", handled=True, scope=scope
            )
        try:
            write_export_file(Path(target_path), png_bytes)
        except Sam3AssetExportError as exc:
            return self._blocked_asset_export_result(action_id, scope=scope, code=exc.code)
        return self._completed_asset_export_result(action_id, scope=scope, file_count=1)

    def _execute_context_export_all_candidates_action(  # noqa: PLR0911 - gate ladder
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        scope = "candidates"
        try:
            store, session, source_layer, canvas_size = self._require_asset_export_batch_context(
                canvas=canvas, asset_store=asset_store
            )
        except Sam3AISelectToolStateError as exc:
            return self._blocked_asset_export_result(action_id, scope=scope, code=exc.code)

        exportable, skipped = _enumerate_exportable_candidates(session)
        if not exportable:
            return self._blocked_asset_export_result(
                action_id,
                scope=scope,
                code="asset_export_candidates_missing",
                skipped_count=skipped,
            )

        # Capture immutable inputs before the modal dialog (mask assets are
        # persisted on disk; the layer pixels are copied), then stream one
        # sprite at a time so peak memory stays at a single candidate.
        captured_layer = _captured_export_layer(source_layer)
        padding_px = int(self.settings.export_padding_px)
        output_dir = _prompt_asset_export_directory()
        if not output_dir:
            return _asset_export_action_result_payload(
                action_id, status="cancelled", handled=True, scope=scope
            )

        empty_skips = 0

        def _sprite_files() -> Iterable[tuple[str, bytes]]:
            nonlocal empty_skips
            for index, candidate_mask_id in exportable:
                try:
                    png_bytes = self._build_candidate_export_png(
                        store,
                        candidate_mask_id,
                        source_layer=captured_layer,
                        canvas_size=canvas_size,
                        padding_px=padding_px,
                    )
                except Sam3AISelectToolStateError:
                    empty_skips += 1
                    continue
                yield (f"asset_{index:02d}.png", png_bytes)

        try:
            result = write_export_batch(Path(output_dir), _sprite_files())
        except Sam3AssetExportWriteError as exc:
            return self._blocked_asset_export_result(
                action_id,
                scope=scope,
                code=exc.code,
                file_count=exc.written,
                skipped_count=skipped + empty_skips,
            )
        except Sam3AssetExportError as exc:
            return self._blocked_asset_export_result(action_id, scope=scope, code=exc.code)
        total_skipped = skipped + empty_skips
        if result.written == 0:
            return self._blocked_asset_export_result(
                action_id,
                scope=scope,
                code="asset_export_empty",
                skipped_count=total_skipped,
            )
        return self._completed_asset_export_result(
            action_id,
            scope=scope,
            file_count=result.written,
            skipped_count=total_skipped,
        )

    def _execute_context_export_sprite_sheet_action(  # noqa: PLR0911 - gate ladder
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        scope = "sprite_sheet"
        try:
            store, session, source_layer, canvas_size = self._require_asset_export_batch_context(
                canvas=canvas, asset_store=asset_store
            )
        except Sam3AISelectToolStateError as exc:
            return self._blocked_asset_export_result(action_id, scope=scope, code=exc.code)

        # Lightweight target check first: sprite generation never starts for
        # an all-rejected / all-maskless set.
        exportable, skipped = _enumerate_exportable_candidates(session)
        if not exportable:
            return self._blocked_asset_export_result(
                action_id,
                scope=scope,
                code="asset_export_candidates_missing",
                skipped_count=skipped,
            )

        captured_layer = _captured_export_layer(source_layer)
        padding_px = int(self.settings.export_padding_px)
        spacing_px = int(self.settings.sheet_spacing_px)
        power_of_two = bool(self.settings.sheet_power_of_two)
        image_format = str(self.settings.sheet_format)
        max_side = int(sheet_max_side_px())
        area_budget = max_side * max_side
        frames: list[SpriteSheetFrame] = []
        empty_skips = 0
        accumulated_area = 0
        for index, candidate_mask_id in exportable:
            try:
                trimmed, trim_bbox, source_size = self._build_candidate_trimmed_export_image(
                    store,
                    candidate_mask_id,
                    source_layer=captured_layer,
                    canvas_size=canvas_size,
                    padding_px=padding_px,
                )
            except Sam3AISelectToolStateError:
                empty_skips += 1
                continue
            # Early abort with the configured spacing: a sheet doomed to exceed
            # the texture limit must not keep materializing sprites first.
            accumulated_area += (int(trimmed.width()) + spacing_px) * (
                int(trimmed.height()) + spacing_px
            )
            if accumulated_area > area_budget:
                return self._blocked_asset_export_result(
                    action_id,
                    scope=scope,
                    code="asset_export_sheet_too_large",
                    skipped_count=skipped + empty_skips,
                )
            frames.append(
                SpriteSheetFrame(
                    name=f"asset_{index:02d}",
                    image=trimmed,
                    trim_bbox=trim_bbox,
                    source_size=source_size,
                )
            )
        total_skipped = skipped + empty_skips
        if not frames:
            return self._blocked_asset_export_result(
                action_id,
                scope=scope,
                code="asset_export_empty",
                skipped_count=total_skipped,
            )
        try:
            layout = pack_sprite_sheet(frames, spacing_px=spacing_px, power_of_two=power_of_two)
        except Sam3AssetExportError as exc:
            return self._blocked_asset_export_result(
                action_id, scope=scope, code=exc.code, skipped_count=total_skipped
            )

        # Everything above (including the size guards) runs before the dialog.
        raw_path = _prompt_sprite_sheet_save_path(image_format)
        if not raw_path:
            return _asset_export_action_result_payload(
                action_id, status="cancelled", handled=True, scope=scope
            )
        image_path = _normalized_sheet_image_path(raw_path, image_format=image_format)
        atlas_path = _sheet_atlas_path(image_path, image_format=image_format)
        if atlas_path.exists() and not image_path.exists():
            # A pre-existing .json without an overwrite-confirmed image could
            # be unrelated user data: never overwrite it silently.
            return self._blocked_asset_export_result(
                action_id,
                scope=scope,
                code="asset_export_atlas_conflict",
                skipped_count=total_skipped,
            )

        try:
            sheet_bytes = encode_sprite_sheet_image(
                compose_sprite_sheet_image(layout), image_format=image_format
            )
            write_export_file(image_path, sheet_bytes)
        except Sam3AssetExportError as exc:
            return self._blocked_asset_export_result(
                action_id, scope=scope, code=exc.code, skipped_count=total_skipped
            )
        atlas_payload = build_sprite_atlas_payload(layout, image_name=image_path.name)
        atlas_bytes = json.dumps(atlas_payload, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            write_export_file(atlas_path, atlas_bytes)
        except Sam3AssetExportError as exc:
            # The sheet PNG stays on disk (partial result, Loop 18 semantics).
            return self._blocked_asset_export_result(
                action_id,
                scope=scope,
                code=exc.code,
                file_count=1,
                skipped_count=total_skipped,
            )
        return self._completed_asset_export_result(
            action_id,
            scope=scope,
            file_count=2,
            skipped_count=total_skipped,
        )

    def _execute_context_export_selection_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
    ) -> dict[str, Any]:
        scope = "selection"
        try:
            selection_array, selection_bounds = self._require_export_selection(canvas)
            source_layer_id = _canvas_active_layer_id(canvas)
            if source_layer_id is None:
                raise Sam3AISelectToolStateError(
                    "target_layer_missing",
                    "SAM3 selection export requires an active layer.",
                )
            canvas_size = _canvas_size(canvas)
            if canvas_size is None:
                raise Sam3AISelectToolStateError(
                    "invalid_canvas_size",
                    "SAM3 selection export requires a valid canvas size.",
                )
            source_layer = self._resolve_export_source(
                canvas, canvas_size=canvas_size, active_layer_id=source_layer_id
            )
            image = build_selection_export_image(
                source_layer.image,
                selection_array,
                selection_bounds=selection_bounds,
                layer_offset=(
                    int(getattr(source_layer, "offset_x", 0)),
                    int(getattr(source_layer, "offset_y", 0)),
                ),
            )
            png_bytes = self._encode_trimmed_export_png(image)
        except Sam3AISelectToolStateError as exc:
            return self._blocked_asset_export_result(action_id, scope=scope, code=exc.code)

        target_path = _prompt_asset_export_save_path("selection.png")
        if not target_path:
            return _asset_export_action_result_payload(
                action_id, status="cancelled", handled=True, scope=scope
            )
        try:
            write_export_file(Path(target_path), png_bytes)
        except Sam3AssetExportError as exc:
            return self._blocked_asset_export_result(action_id, scope=scope, code=exc.code)
        return self._completed_asset_export_result(action_id, scope=scope, file_count=1)

    def _build_candidate_export_png(
        self,
        store: Sam3AssetStore,
        mask_asset_id: str,
        *,
        source_layer: Any,
        canvas_size: tuple[int, int],
        padding_px: int | None = None,
    ) -> bytes:
        trimmed, _bbox, _source_size = self._build_candidate_trimmed_export_image(
            store,
            mask_asset_id,
            source_layer=source_layer,
            canvas_size=canvas_size,
            padding_px=padding_px,
        )
        try:
            return encode_transparent_png(trimmed)
        except Sam3AssetExportError as exc:
            # Normalize encode failures into the blocked flow: the single-path
            # handlers only catch the tool state error.
            raise Sam3AISelectToolStateError(exc.code, str(exc)) from exc

    def _build_candidate_trimmed_export_image(
        self,
        store: Sam3AssetStore,
        mask_asset_id: str,
        *,
        source_layer: Any,
        canvas_size: tuple[int, int],
        padding_px: int | None = None,
    ) -> tuple[Any, tuple[int, int, int, int], tuple[int, int]]:
        """Trimmed sprite + layer-local bbox + source (layer) size."""

        try:
            mask_image = build_layer_mask_image_from_asset(
                store,
                mask_asset_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
            )
        except Sam3LayerMaskAdapterError as exc:
            raise Sam3AISelectToolStateError(exc.code, str(exc)) from exc
        masked = build_masked_layer_image(source_layer, mask_image)
        if masked is None:
            raise Sam3AISelectToolStateError(
                "asset_export_empty",
                "SAM3 asset export produced no pixels.",
            )
        source_size = (int(masked.width()), int(masked.height()))
        padding = int(self.settings.export_padding_px) if padding_px is None else int(padding_px)
        trimmed = trim_image_to_alpha_bbox(masked, padding_px=padding)
        if trimmed is None:
            raise Sam3AISelectToolStateError(
                "asset_export_empty",
                "SAM3 asset export produced no pixels.",
            )
        return trimmed[0], trimmed[1], source_size

    def _encode_trimmed_export_png(
        self,
        image: Any,
        *,
        padding_px: int | None = None,
    ) -> bytes:
        if image is None:
            raise Sam3AISelectToolStateError(
                "asset_export_empty",
                "SAM3 asset export produced no pixels.",
            )
        padding = int(self.settings.export_padding_px) if padding_px is None else int(padding_px)
        trimmed = trim_image_to_alpha_bbox(image, padding_px=padding)
        if trimmed is None:
            raise Sam3AISelectToolStateError(
                "asset_export_empty",
                "SAM3 asset export produced no pixels.",
            )
        try:
            return encode_transparent_png(trimmed[0])
        except Sam3AssetExportError as exc:
            # Normalize encode failures into the blocked flow: the single-path
            # handlers only catch the tool state error.
            raise Sam3AISelectToolStateError(exc.code, str(exc)) from exc

    def _require_asset_export_batch_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
    ) -> tuple[Sam3AssetStore, Sam3SelectionSession, Any, tuple[int, int]]:
        # Unlike the single-candidate gate this does not require a selected
        # candidate: an all-rejected set must reach the handler and report
        # asset_export_candidates_missing instead of a selection error.
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 asset export requires a canvas.",
            )
        if asset_store is None:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            )
        session = self._require_session()
        source_layer_id = _canvas_active_layer_id(canvas)
        if source_layer_id is None:
            raise Sam3AISelectToolStateError(
                "target_layer_missing",
                "SAM3 asset export requires an active layer.",
            )
        canvas_size = _canvas_size(canvas)
        if canvas_size is None:
            raise Sam3AISelectToolStateError(
                "invalid_canvas_size",
                "SAM3 asset export requires a valid canvas size.",
            )
        source_layer = self._resolve_export_source(
            canvas, canvas_size=canvas_size, active_layer_id=source_layer_id
        )
        return (asset_store, session, source_layer, canvas_size)

    def _require_export_source_layer(self, canvas: object | None, source_layer_id: str) -> Any:
        from loomstroke.core.command_layer_access import _resolve_layer_by_id  # noqa: PLC0415

        source_layer = _resolve_layer_by_id(canvas, source_layer_id)
        if source_layer is None:
            raise Sam3AISelectToolStateError(
                "target_layer_missing",
                "SAM3 asset export requires an active layer.",
            )
        source_image = getattr(source_layer, "image", None)
        if source_image is None or not hasattr(source_image, "isNull") or source_image.isNull():
            raise Sam3AISelectToolStateError(
                "asset_export_layer_image_missing",
                "SAM3 asset export requires a raster layer with image pixels.",
            )
        return source_layer

    def _resolve_export_source(
        self,
        canvas: object | None,
        *,
        canvas_size: tuple[int, int],
        active_layer_id: str,
    ) -> Any:
        """Return the pixel source layer for export.

        ``export_source == "composite"`` reads the visible composite (all
        layers, as seen) via a canvas-sized offset-0 pseudo layer; otherwise
        the active layer's own pixels are used. The active-layer existence
        gate stays in the caller either way (a canvas with no layers has no
        composite worth exporting).
        """

        if self.settings.export_source == "composite":
            layer, blocked_code = self._resolve_export_composite_layer(
                canvas, canvas_size=canvas_size
            )
            if layer is None:
                assert blocked_code is not None
                raise Sam3AISelectToolStateError(
                    blocked_code,
                    "SAM3 asset export could not read the visible composite.",
                )
            logger.debug("SAM3 asset export source=composite")
            return layer
        logger.debug("SAM3 asset export source=active_layer")
        return self._require_export_source_layer(canvas, active_layer_id)

    def _resolve_export_composite_layer(  # noqa: PLR0911 - flat guard chain
        self,
        canvas: object | None,
        *,
        canvas_size: tuple[int, int],
    ) -> tuple[Any | None, str | None]:
        """(pseudo layer, None) on success / (None, blocked_code) on failure.

        Mirrors the two-stage guard of the diff QA capture: the declared
        canvas size is checked before the expensive readback, and the read
        image must match the canvas size exactly (a DPR!=1 or stale-fallback
        mismatch would misalign the mask / selection multiply). Never raises
        (like the diff QA capture): any failure maps to a blocked code."""

        getter = getattr(canvas, "get_composited_pixmap", None) if canvas is not None else None
        if not callable(getter):
            return (None, "asset_export_composite_unavailable")
        if canvas_size[0] * canvas_size[1] > _EXPORT_COMPOSITE_MAX_CANVAS_PX:
            return (None, "asset_export_composite_too_large")
        try:
            from PySide6.QtGui import QImage, QPixmap  # noqa: PLC0415

            pixmap = getter()
            if not isinstance(pixmap, QPixmap) or pixmap.isNull():
                return (None, "asset_export_composite_unavailable")
            image = pixmap.toImage()
            if image.isNull() or image.width() <= 0 or image.height() <= 0:
                return (None, "asset_export_composite_unavailable")
            if (image.width(), image.height()) != canvas_size:
                return (None, "asset_export_composite_unavailable")
            premultiplied = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied).copy()
        except Exception:
            logger.debug("SAM3 asset export composite readback failed", exc_info=True)
            return (None, "asset_export_composite_unavailable")
        from types import SimpleNamespace  # noqa: PLC0415

        # _sam3_export_owned marks this as an already-copied immutable source
        # so _captured_export_layer skips a redundant second copy.
        return (
            SimpleNamespace(
                image=premultiplied,
                offset_x=0,
                offset_y=0,
                _sam3_export_owned=True,
            ),
            None,
        )

    def _require_export_selection(
        self,
        canvas: object | None,
    ) -> tuple[Any, tuple[int, int, int, int]]:
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 selection export requires a canvas.",
            )
        get_selection_mask = getattr(canvas, "get_selection_mask", None)
        get_selection_bounds = getattr(canvas, "get_selection_bounds", None)
        selection_mask = get_selection_mask() if callable(get_selection_mask) else None
        selection_rect = get_selection_bounds() if callable(get_selection_bounds) else None
        if selection_mask is None or selection_rect is None or selection_rect.isEmpty():
            raise Sam3AISelectToolStateError(
                "selection_missing",
                "SAM3 selection export requires an active selection.",
            )
        selection_array = _selection_mask_to_array(selection_mask)
        if not bool(selection_array.any()):
            raise Sam3AISelectToolStateError(
                "selection_empty",
                "SAM3 selection export requires a non-empty selection.",
            )
        selection_bounds = (
            int(selection_rect.x()),
            int(selection_rect.y()),
            int(selection_rect.width()),
            int(selection_rect.height()),
        )
        return selection_array, selection_bounds

    def _blocked_asset_export_result(
        self,
        action_id: str,
        *,
        scope: str,
        code: str,
        file_count: int = 0,
        skipped_count: int = 0,
    ) -> dict[str, Any]:
        self._emit(
            "sam3_ai_select.asset_export_blocked",
            _asset_export_event_payload(
                scope=scope,
                status="blocked",
                file_count=file_count,
                skipped_count=skipped_count,
                blocked_reason_codes=[code],
            ),
        )
        return _asset_export_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            scope=scope,
            file_count=file_count,
            skipped_count=skipped_count,
            blocked_reason_codes=[code],
        )

    def _completed_asset_export_result(
        self,
        action_id: str,
        *,
        scope: str,
        file_count: int,
        skipped_count: int = 0,
    ) -> dict[str, Any]:
        self._emit(
            "sam3_ai_select.asset_export_completed",
            _asset_export_event_payload(
                scope=scope,
                status="exported",
                file_count=file_count,
                skipped_count=skipped_count,
                blocked_reason_codes=[],
            ),
        )
        return _asset_export_action_result_payload(
            action_id,
            status="exported",
            handled=True,
            scope=scope,
            file_count=file_count,
            skipped_count=skipped_count,
        )

    def _execute_context_cut_to_layer_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        try:
            store, session, candidate, mask_asset_id, source_layer_id, canvas_size = (
                self._require_layer_mask_apply_context(
                    canvas=canvas,
                    asset_store=asset_store,
                )
            )
        except Sam3AISelectToolStateError as exc:
            return _cut_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            command = build_cut_to_new_layer_command(
                store,
                mask_asset_id,
                source_layer_id=source_layer_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                layer_name="SAM3 Cut",
            )
        except Sam3CutToLayerAdapterError as exc:
            return _cut_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return _cut_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )

        try:
            submitted = bool(submit(command, merge_key=merge_key))
        except Exception:
            logger.debug("SAM3 cut-to-layer command submit failed", exc_info=True)
            submitted = False

        created_layer_id = str(getattr(command, "created_layer_id", "") or "").strip() or None
        if not submitted:
            return _cut_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                submitted=False,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                source_layer_id=source_layer_id,
                created_layer_id=created_layer_id,
                blocked_reason_codes=["cut_to_layer_rejected"],
            )

        payload = _cut_to_layer_payload(
            applied=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            source_layer_id=source_layer_id,
            created_layer_id=created_layer_id,
        )
        self._emit("sam3_ai_select.cut_to_layer_applied", payload)
        return _cut_to_layer_action_result_payload(
            action_id,
            status="applied",
            handled=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            source_layer_id=source_layer_id,
            created_layer_id=created_layer_id,
        )

    def _execute_context_extract_to_layer_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        mode = _extract_mode_from_action_id(action_id)
        if mode is None:
            return _extract_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            store, session, candidate, mask_asset_id, source_layer_id, canvas_size = (
                self._require_layer_mask_apply_context(
                    canvas=canvas,
                    asset_store=asset_store,
                )
            )
        except Sam3AISelectToolStateError as exc:
            return _extract_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        try:
            command = build_extract_to_new_layer_command(
                store,
                mask_asset_id,
                source_layer_id=source_layer_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                mode=mode,
                layer_name=_extract_layer_name(mode),
            )
        except Sam3ExtractLayerAdapterError as exc:
            return _extract_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return _extract_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )

        try:
            submitted = bool(submit(command, merge_key=merge_key))
        except Exception:
            logger.debug("SAM3 extract-to-layer command submit failed", exc_info=True)
            submitted = False

        created_layer_id = str(getattr(command, "created_layer_id", "") or "").strip() or None
        if not submitted:
            return _extract_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                mode=mode,
                submitted=False,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                source_layer_id=source_layer_id,
                created_layer_id=created_layer_id,
                blocked_reason_codes=["extract_to_layer_rejected"],
            )

        payload = _extract_to_layer_payload(
            mode=mode,
            applied=True,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            source_layer_id=source_layer_id,
            created_layer_id=created_layer_id,
        )
        self._emit("sam3_ai_select.extract_to_layer_applied", payload)
        return _extract_to_layer_action_result_payload(
            action_id,
            status="applied",
            handled=True,
            mode=mode,
            submitted=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            source_layer_id=source_layer_id,
            created_layer_id=created_layer_id,
        )

    def _execute_context_candidate_layer_mask_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        candidate_id = _candidate_id_from_apply_layer_mask_action_id(action_id)
        if candidate_id is None:
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            session = self._require_session()
            if session.selected_candidate_id != candidate_id:
                self.select_candidate(candidate_id)
        except (Sam3AISelectToolStateError, Sam3SelectionSessionError) as exc:
            return _layer_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return self._execute_context_layer_mask_apply_action(
            action_id,
            canvas=canvas,
            merge_key=merge_key,
            asset_store=asset_store,
        )

    def _execute_context_candidate_ai_effect_mask_slot_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
        exclude_blend: bool,
        background_generation: bool = False,
    ) -> dict[str, Any]:
        if background_generation:
            candidate_id = _candidate_id_from_use_background_generation_mask_action_id(action_id)
        else:
            candidate_id = _candidate_id_from_use_inpaint_mask_action_id(action_id)
        if candidate_id is None:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            session = self._require_session()
            if session.selected_candidate_id != candidate_id:
                self.select_candidate(candidate_id)
        except (Sam3AISelectToolStateError, Sam3SelectionSessionError) as exc:
            return _ai_effect_mask_slot_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return self._execute_context_ai_effect_mask_slot_action(
            action_id,
            canvas=canvas,
            merge_key=merge_key,
            asset_store=asset_store,
            effect_layer_id=effect_layer_id,
            slot_id=slot_id,
            exclude_blend=exclude_blend,
            background_generation=background_generation,
        )

    def _execute_context_candidate_ai_effect_preview_request_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
        force_regenerate: bool,
        preview_param_values: Mapping[str, object] | None,
        preview_slot_refs: Mapping[str, object] | None,
        preview_param_ui_state: Mapping[str, object] | None,
        preview_replace_param_values: bool,
        preview_replace_slot_refs: bool,
        preview_replace_param_ui_state: bool,
        preview_trigger: str | None,
        preview_target_server_id: str | None,
        preview_output_mode_override: str | None,
        preview_metadata: Mapping[str, object] | None,
    ) -> dict[str, Any]:
        candidate_id = _candidate_id_from_preview_inpaint_action_id(action_id)
        if candidate_id is None:
            return _ai_effect_preview_request_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            session = self._require_session()
            if candidate_id not in set(session.available_candidate_ids):
                raise Sam3AISelectToolStateError(
                    "candidate_unavailable",
                    "SAM3 AI Select candidate is not available.",
                )
            if session.selected_candidate_id != candidate_id:
                raise Sam3AISelectToolStateError(
                    "candidate_preview_requires_selected_candidate",
                    "SAM3 AI Select row preview requires the row candidate to be selected.",
                )
        except Sam3AISelectToolStateError as exc:
            return _ai_effect_preview_request_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return self._execute_context_ai_effect_preview_request_action(
            action_id,
            canvas=canvas,
            asset_store=asset_store,
            effect_layer_id=effect_layer_id,
            slot_id=slot_id,
            force_regenerate=force_regenerate,
            preview_param_values=preview_param_values,
            preview_slot_refs=preview_slot_refs,
            preview_param_ui_state=preview_param_ui_state,
            preview_replace_param_values=preview_replace_param_values,
            preview_replace_slot_refs=preview_replace_slot_refs,
            preview_replace_param_ui_state=preview_replace_param_ui_state,
            preview_trigger=preview_trigger,
            preview_target_server_id=preview_target_server_id,
            preview_output_mode_override=preview_output_mode_override,
            preview_metadata=preview_metadata,
        )

    def _execute_context_candidate_clipping_mask_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        candidate_id = _candidate_id_from_apply_clipping_mask_action_id(action_id)
        if candidate_id is None:
            return _clipping_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            session = self._require_session()
            if session.selected_candidate_id != candidate_id:
                self.select_candidate(candidate_id)
        except (Sam3AISelectToolStateError, Sam3SelectionSessionError) as exc:
            return _clipping_mask_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return self._execute_context_clipping_mask_apply_action(
            action_id,
            canvas=canvas,
            merge_key=merge_key,
            asset_store=asset_store,
        )

    def _execute_context_candidate_cut_to_layer_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        candidate_id = _candidate_id_from_cut_to_layer_action_id(action_id)
        if candidate_id is None:
            return _cut_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            session = self._require_session()
            if session.selected_candidate_id != candidate_id:
                self.select_candidate(candidate_id)
        except (Sam3AISelectToolStateError, Sam3SelectionSessionError) as exc:
            return _cut_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return self._execute_context_cut_to_layer_action(
            action_id,
            canvas=canvas,
            merge_key=merge_key,
            asset_store=asset_store,
        )

    def _execute_context_candidate_extract_to_layer_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
    ) -> dict[str, Any]:
        candidate_id = _candidate_id_from_extract_to_layer_action_id(action_id)
        if candidate_id is None:
            return _extract_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            session = self._require_session()
            if session.selected_candidate_id != candidate_id:
                self.select_candidate(candidate_id)
        except (Sam3AISelectToolStateError, Sam3SelectionSessionError) as exc:
            return _extract_to_layer_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return self._execute_context_extract_to_layer_action(
            action_id,
            canvas=canvas,
            merge_key=merge_key,
            asset_store=asset_store,
        )

    def _execute_context_combine_selected_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        selected_candidate_ids: Iterable[str] | None,
        combine_mode: object | None,
    ) -> dict[str, Any]:
        try:
            store, session, candidates, mode, canvas_size = self._require_combine_selected_context(
                canvas=canvas,
                asset_store=asset_store,
                selected_candidate_ids=selected_candidate_ids,
                combine_mode=combine_mode,
            )
        except Sam3AISelectToolStateError as exc:
            return _combine_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        source_candidate_ids = [candidate.candidate_id for candidate in candidates]
        source_mask_asset_ids = [str(candidate.mask_asset_id).strip() for candidate in candidates]
        output_asset_id = _default_combine_asset_id()
        combined_candidate_id = _default_combine_candidate_id()
        try:
            mask_asset = combine_mask_assets(
                store,
                source_mask_asset_ids,
                output_asset_id=output_asset_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                mode=mode,
                metadata={
                    "operation": "combine_selected",
                    "candidateSetId": session.candidate_set.set_id,
                    "sourceCandidateIds": source_candidate_ids,
                },
            )
        except Sam3MaskCombineAdapterError as exc:
            return _combine_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                mode=mode,
                source_candidate_ids=source_candidate_ids,
                blocked_reason_codes=[exc.code],
            )

        combined_candidate = _combined_mask_candidate(
            session,
            candidate_id=combined_candidate_id,
            mask_asset_id=mask_asset.asset_id,
            mask_asset_bbox=mask_asset.bbox,
            mask_asset_width=mask_asset.width,
            mask_asset_height=mask_asset.height,
            mode=mode,
            source_candidate_ids=source_candidate_ids,
            source_mask_asset_ids=source_mask_asset_ids,
        )
        candidate_set = replace(
            session.candidate_set,
            candidates=(*session.candidate_set.candidates, combined_candidate),
        )
        updated_session = replace(
            session,
            candidate_set=candidate_set,
            selected_candidate_id=combined_candidate.candidate_id,
        )
        self._replace_session(updated_session, action="combine")
        payload = _combine_payload(
            combined=True,
            candidate_set_id=candidate_set.set_id,
            candidate_id=combined_candidate.candidate_id,
            mask_asset_id=mask_asset.asset_id,
            mode=mode,
            source_candidate_ids=source_candidate_ids,
        )
        self._emit("sam3_ai_select.mask_combined", payload)
        return _combine_action_result_payload(
            action_id,
            status="combined",
            handled=True,
            combined=True,
            candidate_set_id=candidate_set.set_id,
            candidate_id=combined_candidate.candidate_id,
            mask_asset_id=mask_asset.asset_id,
            mode=mode,
            source_candidate_ids=source_candidate_ids,
            session=self._last_session,
        )

    def _execute_context_mask_refine_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        mask_refine_options: Mapping[str, object] | None,
    ) -> dict[str, Any]:
        try:
            store, session, candidate, canvas_size = self._require_mask_refine_context(
                canvas=canvas,
                asset_store=asset_store,
            )
        except Sam3AISelectToolStateError as exc:
            return _mask_refine_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        if mask_refine_options is None:
            # Toolbar path: no panel supplied dialog options, so the tool
            # opens the morphology dialog itself when a GUI is available.
            mask_refine_options = self._prompt_mask_refine_options()
            if mask_refine_options is None:
                return _mask_refine_action_result_payload(
                    action_id,
                    status="blocked",
                    handled=False,
                    base_candidate_id=candidate.candidate_id,
                    blocked_reason_codes=["mask_refine_options_noop"],
                )

        options = MaskRefineOptions.from_dict(dict(mask_refine_options or {})).normalized()
        if options.is_noop():
            return _mask_refine_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                base_candidate_id=candidate.candidate_id,
                blocked_reason_codes=["mask_refine_options_noop"],
            )

        output_asset_id = _default_refined_asset_id()
        try:
            mask_asset = refine_mask_asset(
                store,
                str(candidate.mask_asset_id),
                output_asset_id=output_asset_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                options=options,
                source_candidate_id=candidate.candidate_id,
                metadata={
                    "operation": "mask_refine_selected",
                    "candidateSetId": session.candidate_set.set_id,
                },
            )
        except Sam3MaskRefineAdapterError as exc:
            return _mask_refine_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                base_candidate_id=candidate.candidate_id,
                blocked_reason_codes=[exc.code],
            )

        refined_candidate = _refined_mask_candidate(
            session,
            base_candidate=candidate,
            candidate_id=_default_refined_candidate_id(),
            mask_asset_id=mask_asset.asset_id,
            mask_asset_bbox=mask_asset.bbox,
            mask_asset_width=mask_asset.width,
            mask_asset_height=mask_asset.height,
            options=options,
        )
        candidate_set = replace(
            session.candidate_set,
            candidates=(*session.candidate_set.candidates, refined_candidate),
        )
        updated_session = replace(
            session,
            candidate_set=candidate_set,
            selected_candidate_id=refined_candidate.candidate_id,
        )
        self._replace_session(updated_session, action="mask_refine")
        payload = _mask_refine_payload(
            refined=True,
            candidate_set_id=candidate_set.set_id,
            base_candidate_id=candidate.candidate_id,
            base_mask_asset_id=str(candidate.mask_asset_id),
            refined_candidate_id=refined_candidate.candidate_id,
            refined_mask_asset_id=mask_asset.asset_id,
            options=options.to_dict(),
        )
        self._emit("sam3_ai_select.mask_refined", payload)
        return _mask_refine_action_result_payload(
            action_id,
            status="refined",
            handled=True,
            refined=True,
            candidate_set_id=candidate_set.set_id,
            base_candidate_id=candidate.candidate_id,
            base_mask_asset_id=str(candidate.mask_asset_id),
            refined_candidate_id=refined_candidate.candidate_id,
            refined_mask_asset_id=mask_asset.asset_id,
            options=options.to_dict(),
            session=self._last_session,
        )

    def _require_mask_refine_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
    ) -> tuple[Sam3AssetStore, Sam3SelectionSession, MaskCandidate, tuple[int, int]]:
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 mask refine requires a canvas.",
            )
        if asset_store is None:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            )
        session = self._require_session()
        canvas_size = _canvas_size(canvas)
        if canvas_size is None:
            raise Sam3AISelectToolStateError(
                "invalid_canvas_size",
                "SAM3 mask refine requires a valid canvas size.",
            )
        candidate = session.active_candidate
        if candidate is None:
            raise Sam3AISelectToolStateError(
                "candidate_selection_missing",
                "SAM3 mask refine requires a selected candidate.",
            )
        if not str(candidate.mask_asset_id or "").strip():
            raise Sam3AISelectToolStateError(
                "candidate_mask_missing",
                "SAM3 mask refine candidate has no mask asset.",
            )
        return (asset_store, session, candidate, canvas_size)

    def _execute_context_candidate_group_to_layer_group_action(
        self,
        action_id: str,
        *,
        canvas: object | None,
        merge_key: str | None,
        asset_store: Sam3AssetStore | None,
        selected_candidate_ids: Iterable[str] | None,
    ) -> dict[str, Any]:
        try:
            store, session, candidates, source_layer_id, canvas_size = (
                self._require_candidate_group_to_layer_group_context(
                    canvas=canvas,
                    asset_store=asset_store,
                    selected_candidate_ids=selected_candidate_ids,
                )
            )
        except Sam3AISelectToolStateError as exc:
            return _candidate_group_to_layer_group_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        source_candidate_ids = [candidate.candidate_id for candidate in candidates]
        try:
            command = build_candidate_group_to_layer_group_command(
                store,
                candidates,
                source_layer_id=source_layer_id,
                canvas_width=canvas_size[0],
                canvas_height=canvas_size[1],
                group_name="SAM3 Candidates",
            )
        except Sam3ExtractLayerAdapterError as exc:
            return _candidate_group_to_layer_group_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        submit = getattr(canvas, "submit", None)
        if not callable(submit):
            return _candidate_group_to_layer_group_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_canvas_missing"],
            )

        try:
            submitted = bool(submit(command, merge_key=merge_key))
        except Exception:
            logger.debug("SAM3 candidate group-to-layer-group command submit failed", exc_info=True)
            submitted = False

        created_layer_ids = tuple(getattr(command, "created_layer_ids", ()) or ())
        created_group_layer_id = (
            str(getattr(command, "created_group_layer_id", "") or "").strip() or None
        )
        if not submitted:
            return _candidate_group_to_layer_group_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                blocked_reason_codes=["candidate_group_to_layer_group_rejected"],
            )

        payload = _candidate_group_to_layer_group_payload(
            created=True,
            candidate_set_id=session.candidate_set.set_id,
            source_candidate_ids=source_candidate_ids,
            source_layer_id=source_layer_id,
            created_layer_ids=created_layer_ids,
            created_group_layer_id=created_group_layer_id,
        )
        self._emit("sam3_ai_select.layer_group_created", payload)
        return _candidate_group_to_layer_group_action_result_payload(
            action_id,
            status="created",
            handled=True,
            candidate_set_id=session.candidate_set.set_id,
            source_candidate_ids=source_candidate_ids,
            source_layer_id=source_layer_id,
            created_layer_ids=created_layer_ids,
            created_group_layer_id=created_group_layer_id,
        )

    def _execute_context_save_mask_asset_action(
        self,
        action_id: str,
        *,
        asset_store: Sam3AssetStore | None,
        history_store: object | None,
    ) -> dict[str, Any]:
        try:
            session, candidate, mask_asset = self._require_save_mask_asset_context(
                asset_store=asset_store,
                history_store=history_store,
            )
        except Sam3AISelectToolStateError as exc:
            return _save_mask_asset_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        record = _build_save_mask_asset_generation_record(
            settings=self.settings,
            session=session,
            candidate=candidate,
            mask_asset=mask_asset,
        )
        add_record = getattr(history_store, "add_record", None)
        if not callable(add_record):
            return _save_mask_asset_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                mask_asset_id=mask_asset.asset_id,
                blocked_reason_codes=["generation_history_store_unavailable"],
            )

        try:
            add_record(record)
        except Exception:
            logger.debug("SAM3 save-mask-asset history add failed", exc_info=True)
            return _save_mask_asset_action_result_payload(
                action_id,
                status="blocked",
                handled=True,
                candidate_set_id=session.candidate_set.set_id,
                candidate_id=candidate.candidate_id,
                mask_asset_id=mask_asset.asset_id,
                blocked_reason_codes=["save_mask_asset_rejected"],
            )

        payload = _save_mask_asset_payload(
            saved=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            mask_asset_id=mask_asset.asset_id,
            history_job_id=record.job_id,
        )
        self._emit("sam3_ai_select.mask_asset_saved", payload)
        return _save_mask_asset_action_result_payload(
            action_id,
            status="saved",
            handled=True,
            saved=True,
            candidate_set_id=session.candidate_set.set_id,
            candidate_id=candidate.candidate_id,
            mask_asset_id=mask_asset.asset_id,
            history_job_id=record.job_id,
        )

    def _execute_context_candidate_save_mask_asset_action(
        self,
        action_id: str,
        *,
        asset_store: Sam3AssetStore | None,
        history_store: object | None,
    ) -> dict[str, Any]:
        candidate_id = _candidate_id_from_save_mask_asset_action_id(action_id)
        if candidate_id is None:
            return _save_mask_asset_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            session = self._require_session()
            if session.selected_candidate_id != candidate_id:
                self.select_candidate(candidate_id)
        except (Sam3AISelectToolStateError, Sam3SelectionSessionError) as exc:
            return _save_mask_asset_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )

        return self._execute_context_save_mask_asset_action(
            action_id,
            asset_store=asset_store,
            history_store=history_store,
        )

    def _require_layer_mask_apply_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
    ) -> tuple[Sam3AssetStore, Sam3SelectionSession, Any, str, str, tuple[int, int]]:
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 layer mask apply requires a canvas.",
            )
        if asset_store is None:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            )

        session = self._require_session()
        candidate = session.active_candidate
        if candidate is None:
            raise Sam3AISelectToolStateError(
                "candidate_selection_missing",
                "No SAM3 AI Select candidate is selected.",
            )
        mask_asset_id = str(candidate.mask_asset_id or "").strip()
        if not mask_asset_id:
            raise Sam3AISelectToolStateError(
                "candidate_mask_missing",
                "Selected SAM3 AI Select candidate has no mask asset.",
            )

        target_layer_id = _canvas_active_layer_id(canvas)
        if target_layer_id is None:
            raise Sam3AISelectToolStateError(
                "target_layer_missing",
                "SAM3 layer mask apply requires an active target layer.",
            )
        canvas_size = _canvas_size(canvas)
        if canvas_size is None:
            raise Sam3AISelectToolStateError(
                "invalid_canvas_size",
                "SAM3 layer mask apply requires a valid canvas size.",
            )
        return (asset_store, session, candidate, mask_asset_id, target_layer_id, canvas_size)

    def _require_ai_effect_mask_slot_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
    ) -> tuple[Sam3AssetStore, Sam3SelectionSession, Any, str, str, str, tuple[int, int]]:
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 AI Effect mask slot binding requires a canvas.",
            )
        if asset_store is None:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            )

        session = self._require_session()
        candidate = session.active_candidate
        if candidate is None:
            raise Sam3AISelectToolStateError(
                "candidate_selection_missing",
                "No SAM3 AI Select candidate is selected.",
            )
        mask_asset_id = str(candidate.mask_asset_id or "").strip()
        if not mask_asset_id:
            raise Sam3AISelectToolStateError(
                "candidate_mask_missing",
                "Selected SAM3 AI Select candidate has no mask asset.",
            )

        target_layer_id, target_slot_id, canvas_size = self._require_ai_effect_target(
            canvas,
            effect_layer_id=effect_layer_id,
            slot_id=slot_id,
        )
        return (
            asset_store,
            session,
            candidate,
            mask_asset_id,
            target_layer_id,
            target_slot_id,
            canvas_size,
        )

    def _require_ai_effect_preview_request_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        effect_layer_id: str | None,
        slot_id: str | None,
    ) -> tuple[Sam3AssetStore, Sam3SelectionSession, Any, str, str, str, tuple[int, int]]:
        (
            store,
            session,
            candidate,
            mask_asset_id,
            target_layer_id,
            target_slot_id,
            canvas_size,
        ) = self._require_ai_effect_mask_slot_context(
            canvas=canvas,
            asset_store=asset_store,
            effect_layer_id=effect_layer_id,
            slot_id=slot_id,
        )
        assert canvas is not None
        target_layer = _canvas_layer_by_id(canvas, target_layer_id)
        state = getattr(target_layer, "ai_effect", None)
        slot_refs = getattr(state, "slot_refs", None)
        if not isinstance(slot_refs, Mapping) or slot_refs.get(target_slot_id) is None:
            raise Sam3AISelectToolStateError(
                "target_ai_effect_mask_slot_missing",
                "SAM3 AI Effect preview request requires a bound mask slot.",
            )
        return (
            store,
            session,
            candidate,
            mask_asset_id,
            target_layer_id,
            target_slot_id,
            canvas_size,
        )

    def _require_combine_selected_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        selected_candidate_ids: Iterable[str] | None,
        combine_mode: object | None,
    ) -> tuple[
        Sam3AssetStore,
        Sam3SelectionSession,
        tuple[MaskCandidate, ...],
        MaskCombineMode,
        tuple[int, int],
    ]:
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 mask combine requires a canvas.",
            )
        if asset_store is None:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            )

        session = self._require_session()
        canvas_size = _canvas_size(canvas)
        if canvas_size is None:
            raise Sam3AISelectToolStateError(
                "invalid_canvas_size",
                "SAM3 mask combine requires a valid canvas size.",
            )

        if selected_candidate_ids is None:
            # Toolbar path: fall back to the canvas Shift+click multi-selection.
            selected_candidate_ids = self._multi_selected_candidate_ids
        candidate_ids = _combine_candidate_ids(selected_candidate_ids)
        if len(candidate_ids) < 2:
            raise Sam3AISelectToolStateError(
                "candidate_multi_selection_missing",
                "SAM3 mask combine requires at least two selected candidates.",
            )

        available_ids = set(session.available_candidate_ids)
        candidates_by_id = {
            candidate.candidate_id: candidate
            for candidate in session.candidate_set.candidates
            if candidate.candidate_id
        }
        candidates: list[MaskCandidate] = []
        for candidate_id in candidate_ids:
            candidate = candidates_by_id.get(candidate_id)
            if candidate is None or candidate_id not in available_ids:
                raise Sam3AISelectToolStateError(
                    "candidate_unavailable",
                    "SAM3 mask combine candidate is not available.",
                )
            if not str(candidate.mask_asset_id or "").strip():
                raise Sam3AISelectToolStateError(
                    "candidate_mask_missing",
                    "SAM3 mask combine candidate has no mask asset.",
                )
            candidates.append(candidate)

        try:
            mode = normalize_mask_combine_mode(
                combine_mode if combine_mode is not None else self.settings.combine_mode
            )
        except Sam3MaskCombineError as exc:
            raise Sam3AISelectToolStateError(exc.code, str(exc)) from exc
        return (asset_store, session, tuple(candidates), mode, canvas_size)

    def _require_candidate_group_to_layer_group_context(
        self,
        *,
        canvas: object | None,
        asset_store: Sam3AssetStore | None,
        selected_candidate_ids: Iterable[str] | None,
    ) -> tuple[
        Sam3AssetStore,
        Sam3SelectionSession,
        tuple[MaskCandidate, ...],
        str,
        tuple[int, int],
    ]:
        if canvas is None:
            raise Sam3AISelectToolStateError(
                "ai_select_canvas_missing",
                "SAM3 candidate layer group requires a canvas.",
            )
        if asset_store is None:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            )

        session = self._require_session()
        canvas_size = _canvas_size(canvas)
        if canvas_size is None:
            raise Sam3AISelectToolStateError(
                "invalid_canvas_size",
                "SAM3 candidate layer group requires a valid canvas size.",
            )
        source_layer_id = _canvas_active_layer_id(canvas)
        if source_layer_id is None:
            raise Sam3AISelectToolStateError(
                "target_layer_missing",
                "SAM3 candidate layer group requires an active source layer.",
            )

        if selected_candidate_ids is None:
            # Toolbar path: fall back to the canvas Shift+click multi-selection.
            selected_candidate_ids = self._multi_selected_candidate_ids
        candidate_ids = _combine_candidate_ids(selected_candidate_ids)
        if len(candidate_ids) < 2:
            raise Sam3AISelectToolStateError(
                "candidate_multi_selection_missing",
                "SAM3 candidate layer group requires at least two selected candidates.",
            )

        available_ids = set(session.available_candidate_ids)
        candidates_by_id = {
            candidate.candidate_id: candidate
            for candidate in session.candidate_set.candidates
            if candidate.candidate_id
        }
        candidates: list[MaskCandidate] = []
        for candidate_id in candidate_ids:
            candidate = candidates_by_id.get(candidate_id)
            if candidate is None or candidate_id not in available_ids:
                raise Sam3AISelectToolStateError(
                    "candidate_unavailable",
                    "SAM3 candidate layer group candidate is not available.",
                )
            if not str(candidate.mask_asset_id or "").strip():
                raise Sam3AISelectToolStateError(
                    "candidate_mask_missing",
                    "SAM3 candidate layer group candidate has no mask asset.",
                )
            candidates.append(candidate)
        return (asset_store, session, tuple(candidates), source_layer_id, canvas_size)

    def _require_save_mask_asset_context(
        self,
        *,
        asset_store: Sam3AssetStore | None,
        history_store: object | None,
    ) -> tuple[Sam3SelectionSession, Any, Any]:
        if asset_store is None:
            raise Sam3AISelectToolStateError(
                "ai_select_asset_store_missing",
                "No SAM3 AI Select asset store is available.",
            )
        if history_store is None:
            raise Sam3AISelectToolStateError(
                "generation_history_store_missing",
                "No generation history store is available.",
            )

        session = self._require_session()
        candidate = session.active_candidate
        if candidate is None:
            raise Sam3AISelectToolStateError(
                "candidate_selection_missing",
                "No SAM3 AI Select candidate is selected.",
            )
        mask_asset_id = str(candidate.mask_asset_id or "").strip()
        if not mask_asset_id:
            raise Sam3AISelectToolStateError(
                "candidate_mask_missing",
                "Selected SAM3 AI Select candidate has no mask asset.",
            )
        try:
            mask_asset = asset_store.load_mask_asset(mask_asset_id)
        except (OSError, ValueError) as exc:
            raise Sam3AISelectToolStateError(
                "mask_asset_unavailable",
                "Selected SAM3 AI Select mask asset is not available.",
            ) from exc
        return (session, candidate, mask_asset)

    def _resolve_generation_history_store(self, explicit: object | None) -> object | None:
        if explicit is not None:
            return explicit
        return _generation_history_store_from_host(self.host_api)

    def _execute_context_session_action(
        self,
        action_id: str,
        action: Mapping[str, Any],
    ) -> dict[str, Any]:
        if action_id in {
            _CONTEXT_ACTION_RUN_STATUS,
            _CONTEXT_ACTION_STATUS,
            _CONTEXT_ACTION_SUMMARY,
        }:
            return _context_action_result_payload(
                action_id,
                status="noop",
                handled=True,
                session=self._last_session,
            )
        if action_id == _CONTEXT_ACTION_PREVIOUS:
            session = self.select_previous_candidate()
        elif action_id == _CONTEXT_ACTION_NEXT:
            session = self.select_next_candidate()
        elif action_id == _CONTEXT_ACTION_REJECT:
            session = self.reject_candidate()
        elif action_id.startswith(
            (
                _CONTEXT_CANDIDATE_SELECT_ACTION_PREFIX,
                _CONTEXT_CANDIDATE_REJECT_ACTION_PREFIX,
            )
        ):
            return self._execute_context_candidate_row_action(action_id)
        elif action_id.startswith(_CONTEXT_OPERATION_ACTION_PREFIX):
            operation = action.get("operation")
            if operation not in SAM3_AI_SELECT_OPERATION_VALUES:
                return _context_action_result_payload(
                    action_id,
                    status="blocked",
                    handled=False,
                    blocked_reason_codes=["unknown_ai_select_context_action"],
                )
            session = self.set_apply_operation(str(operation))
        else:
            return _context_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )
        return _context_action_result_payload(
            action_id,
            status="handled",
            handled=True,
            session=session,
        )

    def _execute_context_candidate_row_action(self, action_id: str) -> dict[str, Any]:
        candidate_id = _candidate_id_from_select_action_id(action_id)
        operation = "select"
        if candidate_id is None:
            candidate_id = _candidate_id_from_reject_action_id(action_id)
            operation = "reject"
        if candidate_id is None:
            return _context_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["unknown_ai_select_context_action"],
            )

        try:
            session = (
                self.select_candidate(candidate_id)
                if operation == "select"
                else self.reject_candidate(candidate_id)
            )
        except (Sam3AISelectToolStateError, Sam3SelectionSessionError) as exc:
            return _context_action_result_payload(
                action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=[exc.code],
            )
        return _context_action_result_payload(
            action_id,
            status="handled",
            handled=True,
            session=session,
        )

    def get_settings(self) -> Sam3AiSelectToolSettings:
        return self.settings

    def set_settings(
        self,
        settings: ToolSettings | Sam3AiSelectToolSettings | Mapping[str, Any],
    ) -> None:
        if isinstance(settings, Sam3AiSelectToolSettings):
            self.settings = settings
            return
        if isinstance(settings, Mapping):
            self._replace_settings(dict(settings))

    def set_setting(self, key: str, value: Any) -> None:
        if key not in _SETTING_KEYS:
            return
        self._replace_settings({key: value})

    def create_options_widget(self) -> None:
        return None

    def context_toolbar_spec(self) -> ContextToolbarSpec:
        return ContextToolbarSpec(
            show_trigger="tool_active",
            property_keys=[],
            custom_widgets=_context_toolbar_widgets(),
            placement="top_center",
            mode="settings",
        )

    def _replace_settings(self, updates: Mapping[str, Any]) -> None:
        payload = _settings_payload(self.settings)
        for key, value in updates.items():
            if key in _SETTING_KEYS:
                payload[key] = value
        self.settings = Sam3AiSelectToolSettings(**payload)

    def _clear_pointer_prompt_drag(self) -> None:
        self._pointer_prompt_start = None
        self._pointer_prompt_label = "positive"
        self._pointer_prompt_shift = False
        self._pointer_prompt_alt = False

    def _tool_registered(self, host: HostAPI) -> bool:
        tool_id = self.descriptor().id
        manager = getattr(host, "_tool_manager", None)
        if manager and hasattr(manager, "get_registered_tools"):
            try:
                for descriptor in manager.get_registered_tools():
                    if descriptor.id == tool_id:
                        return True
            except Exception:
                logger.debug("get_registered_tools failed", exc_info=True)
        pending = getattr(host, "_pending_tools", None)
        if isinstance(pending, list):
            for tool in pending:
                if tool is self:
                    return True
                descriptor_fn = getattr(tool, "descriptor", None)
                if callable(descriptor_fn):
                    try:
                        if descriptor_fn().id == tool_id:
                            return True
                    except Exception:
                        continue
        return False

    def _emit(self, name: str, payload: Mapping[str, Any]) -> None:
        events = getattr(self.host_api, "events", None) if self.host_api is not None else None
        emit = getattr(events, "emit", None)
        if callable(emit):
            emit(name, dict(payload))

    def _require_session(self) -> Sam3SelectionSession:
        if self._last_session is None:
            raise Sam3AISelectToolStateError(
                "ai_select_session_missing",
                "No SAM3 AI Select session is available.",
            )
        return self._last_session

    def _replace_session(
        self,
        session: Sam3SelectionSession,
        *,
        action: str,
    ) -> Sam3SelectionSession:
        run_result = self._last_run_result
        overlay_options = (
            run_result.run_config.overlay_options
            if run_result is not None
            else self.settings.to_overlay_options()
        )
        overlay_model = build_selection_overlay_model(session, overlay_options)
        previous_session = self._last_session
        if (
            previous_session is None
            or previous_session.candidate_set.set_id != session.candidate_set.set_id
        ):
            # A new candidate set invalidates the Shift+click multi-selection.
            self._multi_selected_candidate_ids = ()
        self._last_session = session
        self._last_overlay_model = overlay_model
        self._last_apply_plan = None
        self._last_apply_result = None
        if run_result is not None:
            selection_result = replace(
                run_result.selection_result,
                session=session,
                overlay_model=overlay_model,
                warnings=overlay_model.warnings,
            )
            self._last_run_result = replace(run_result, selection_result=selection_result)
        self._sync_overlay_hook()
        self._refresh_candidate_list_panel()
        self._emit(
            "sam3_ai_select.selection_changed",
            _session_event_payload(session, overlay_model, action),
        )
        return session

    def _apply_completed_refine_result(
        self,
        provider: Sam3MaskProvider,
        result: Sam3SelectionRefineResult,
    ) -> None:
        if result.candidate_set is None or result.session is None or result.overlay_model is None:
            raise Sam3AISelectToolStateError(
                "ai_select_refine_result_incomplete",
                "SAM3 refine completed without a candidate set, session, or overlay.",
            )

        self._last_asset_store = provider.asset_store
        self._replace_session(result.session, action="refine")
        self._last_run_status = _run_status_state_payload("completed")

        if self._last_run_result is None:
            return

        overlay_model = self._last_overlay_model or result.overlay_model
        selection_result = replace(
            self._last_run_result.selection_result,
            status="completed",
            candidate_set=result.candidate_set,
            session=result.session,
            overlay_model=overlay_model,
            blocked_reasons=(),
            warnings=overlay_model.warnings,
            recommended_actions=(),
        )
        self._last_run_result = replace(
            self._last_run_result,
            selection_result=selection_result,
        )

    def _register_candidate_list_panel(self, host: HostAPI) -> None:
        if self._candidate_list_panel_registered:
            return

        register_panel = getattr(host, "register_panel_widget", None)
        if not callable(register_panel):
            return

        has_panel = getattr(host, "has_panel", None)
        if callable(has_panel):
            try:
                if has_panel(CANDIDATE_LIST_PANEL_ID):
                    return
            except Exception:
                logger.debug("SAM3 AI Select panel lookup failed", exc_info=True)

        try:
            from .candidate_list_panel import (  # noqa: PLC0415
                Sam3AISelectCandidateListPanel,
            )

            action_scheduler = _candidate_list_action_scheduler(host)
            panel = Sam3AISelectCandidateListPanel(
                self,
                canvas_provider=lambda: getattr(host, "canvas", None),
                action_scheduler=action_scheduler,
            )
        except Exception:
            logger.warning("SAM3 AI Select candidate list panel creation failed", exc_info=True)
            return

        try:
            registered = bool(
                register_panel(
                    CANDIDATE_LIST_PANEL_ID,
                    _ts("AI Select Candidates"),
                    panel,
                    area="right",
                    default_visible=False,
                    owner_plugin_id=PLUGIN_ID,
                    closable=True,
                    resizable=True,
                    floating=False,
                    default_size=_CANDIDATE_LIST_PANEL_DEFAULT_SIZE,
                )
            )
        except Exception:
            logger.warning("SAM3 AI Select candidate list panel registration failed", exc_info=True)
            registered = False

        if registered:
            self._candidate_list_panel = panel
            self._candidate_list_panel_registered = True
            return

        delete_later = getattr(panel, "deleteLater", None)
        if callable(delete_later):
            try:
                delete_later()
            except Exception:
                logger.debug("SAM3 AI Select candidate list panel delete failed", exc_info=True)

    def _unregister_candidate_list_panel(self) -> None:
        if not self._candidate_list_panel_registered:
            self._candidate_list_panel = None
            return

        panel = self._candidate_list_panel
        binder = getattr(panel, "bind_tool", None)
        if callable(binder):
            try:
                binder(None)
            except Exception:
                logger.debug("SAM3 AI Select candidate list panel unbind failed", exc_info=True)

        self._candidate_list_panel = None
        self._candidate_list_panel_registered = False

        host = self.host_api
        if host is None:
            return
        unregister = getattr(host, "unregister_panel", None)
        if not callable(unregister):
            return
        try:
            unregister(CANDIDATE_LIST_PANEL_ID)
        except Exception:
            logger.debug("SAM3 AI Select candidate list panel unregister failed", exc_info=True)

    def _refresh_candidate_list_panel(self) -> None:
        panel = self._candidate_list_panel
        refresher = getattr(panel, "refresh_from_tool", None)
        if not callable(refresher):
            return
        try:
            refresher()
        except Exception:
            logger.debug("SAM3 AI Select candidate list panel refresh failed", exc_info=True)

    def _register_manga_mask_assistant_panel(self, host: HostAPI) -> None:
        if self._manga_mask_assistant_panel_registered:
            return

        register_panel = getattr(host, "register_panel_widget", None)
        if not callable(register_panel):
            return

        has_panel = getattr(host, "has_panel", None)
        if callable(has_panel):
            try:
                if has_panel(MANGA_MASK_ASSISTANT_PANEL_ID):
                    return
            except Exception:
                logger.debug("SAM3 Manga Mask Assistant panel lookup failed", exc_info=True)

        try:
            from .manga_mask_assistant_panel import (  # noqa: PLC0415
                Sam3MangaMaskAssistantPanel,
            )

            panel = Sam3MangaMaskAssistantPanel(
                self,
                action_scheduler=_candidate_list_action_scheduler(host),
            )
        except Exception:
            logger.warning("SAM3 Manga Mask Assistant panel creation failed", exc_info=True)
            return

        try:
            registered = bool(
                register_panel(
                    MANGA_MASK_ASSISTANT_PANEL_ID,
                    _ts("Manga Mask Assistant"),
                    panel,
                    area="right",
                    default_visible=False,
                    owner_plugin_id=PLUGIN_ID,
                    closable=True,
                    resizable=True,
                    floating=False,
                    default_size=_MANGA_MASK_ASSISTANT_PANEL_DEFAULT_SIZE,
                )
            )
        except Exception:
            logger.warning("SAM3 Manga Mask Assistant panel registration failed", exc_info=True)
            registered = False

        if registered:
            self._manga_mask_assistant_panel = panel
            self._manga_mask_assistant_panel_registered = True
            return

        delete_later = getattr(panel, "deleteLater", None)
        if callable(delete_later):
            try:
                delete_later()
            except Exception:
                logger.debug("SAM3 Manga Mask Assistant panel delete failed", exc_info=True)

    def _unregister_manga_mask_assistant_panel(self) -> None:
        if not self._manga_mask_assistant_panel_registered:
            self._manga_mask_assistant_panel = None
            return

        panel = self._manga_mask_assistant_panel
        binder = getattr(panel, "bind_tool", None)
        if callable(binder):
            try:
                binder(None)
            except Exception:
                logger.debug("SAM3 Manga Mask Assistant panel unbind failed", exc_info=True)

        self._manga_mask_assistant_panel = None
        self._manga_mask_assistant_panel_registered = False

        host = self.host_api
        if host is None:
            return
        unregister = getattr(host, "unregister_panel", None)
        if not callable(unregister):
            return
        try:
            unregister(MANGA_MASK_ASSISTANT_PANEL_ID)
        except Exception:
            logger.debug("SAM3 Manga Mask Assistant panel unregister failed", exc_info=True)

    def _refresh_manga_mask_assistant_panel(self) -> None:
        panel = self._manga_mask_assistant_panel
        refresher = getattr(panel, "refresh_from_tool", None)
        if not callable(refresher):
            return
        try:
            refresher()
        except Exception:
            logger.debug("SAM3 Manga Mask Assistant panel refresh failed", exc_info=True)

    def _sync_overlay_hook(self) -> None:
        if (
            not self._enabled
            or self._last_overlay_model is None
            or not self._candidate_overlay_visible
        ):
            self._clear_overlay_hook()
            return

        widget = self._get_canvas_widget()
        if widget is None:
            return
        setter = getattr(widget, "set_tool_overlay_fn", None)
        if not callable(setter):
            return
        try:
            setter(
                Sam3AiSelectOverlayPainter(
                    self._last_overlay_model,
                    asset_store=self._last_asset_store,
                    multi_selected_ids=self._multi_selected_candidate_ids,
                )
            )
        except Exception:
            logger.debug("SAM3 AI Select overlay install failed", exc_info=True)
            self._overlay_installed = False
            return
        self._overlay_installed = True

    def _clear_overlay_hook(self) -> None:
        if not self._overlay_installed:
            return
        widget = self._get_canvas_widget()
        if widget is not None:
            setter = getattr(widget, "set_tool_overlay_fn", None)
            if callable(setter):
                try:
                    setter(None)
                except Exception:
                    logger.debug("SAM3 AI Select overlay clear failed", exc_info=True)
        self._overlay_installed = False

    def _get_canvas_widget(self) -> Any | None:
        if self.host_api is None:
            return None

        ui = getattr(self.host_api, "ui", None)
        getter = getattr(ui, "get_main_window", None) if ui is not None else None
        if callable(getter):
            try:
                main_window = getter()
            except Exception:
                logger.debug("SAM3 AI Select main window lookup failed", exc_info=True)
            else:
                widget = getattr(main_window, "canvas", None) if main_window is not None else None
                if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
                    return widget

        widget = getattr(self.host_api, "canvas", None)
        if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
            return widget
        return None


def _settings_payload(settings: Sam3AiSelectToolSettings) -> dict[str, Any]:
    return {key: getattr(settings, key) for key in _SETTING_KEYS if hasattr(settings, key)}


def _stroke_point_pos(pt: StrokePoint) -> tuple[float, float]:
    return (float(pt.pos[0]), float(pt.pos[1]))


def _pointer_box_prompt(
    start: tuple[float, float],
    end: tuple[float, float],
    label: PointLabel,
) -> BoxPrompt | None:
    x0, y0 = start
    x1, y1 = end
    x = min(x0, x1)
    y = min(y0, y1)
    width = abs(x1 - x0)
    height = abs(y1 - y0)
    if width < _POINTER_BOX_MIN_SIZE or height < _POINTER_BOX_MIN_SIZE:
        return None
    return BoxPrompt(x, y, width, height, label=label)


def _run_readiness_snapshot_payload(settings: Sam3AiSelectToolSettings) -> dict[str, Any]:
    run_config = settings.to_run_config()
    prompt_spec = run_config.prompt_spec
    blocked_reason_codes: list[str] = []
    try:
        prompt_spec.to_prompt()
    except Sam3SelectionBridgeError as exc:
        blocked_reason_codes.append(exc.code)

    return {
        "schemaVersion": _RUN_READINESS_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "ready": not blocked_reason_codes,
        "mode": prompt_spec.mode,
        "scope": run_config.scope,
        "operation": run_config.operation,
        "maxCandidates": run_config.max_candidates,
        "minScore": run_config.min_score,
        "maskThreshold": run_config.mask_threshold,
        "prompt": {
            "hasText": prompt_spec.prompt_text is not None,
            "hasNegativeText": prompt_spec.negative_text is not None,
            "hasNormalizedText": prompt_spec.normalized_text is not None,
            "pointCount": len(prompt_spec.points),
            "boxCount": len(prompt_spec.boxes),
            "exemplarAssetCount": len(prompt_spec.exemplar_asset_ids),
        },
        "blockedReasonCodes": blocked_reason_codes,
    }


def _bound_run_readiness_snapshot_payload(
    prompt_readiness: Mapping[str, Any],
    *,
    binding: Sam3ProviderBinding | None = None,
) -> dict[str, Any]:
    prompt_ready = bool(prompt_readiness.get("ready", False))
    provider_ready: bool | None = None
    provider_runtime: str | None = None
    provider_warning_codes: list[str] = []
    blocked_reason_codes = list(prompt_readiness.get("blockedReasonCodes", []))
    ready = False

    if prompt_ready and binding is not None:
        provider_ready = bool(binding.ready)
        provider_runtime = binding.runtime
        provider_warning_codes = list(binding.warning_codes)
        if provider_ready:
            ready = True
            blocked_reason_codes = []
        else:
            blocked_reason_codes = list(binding.blocked_reason_codes)
            if not blocked_reason_codes:
                blocked_reason_codes = ["sam3_provider_not_ready"]

    return {
        "schemaVersion": _BOUND_RUN_READINESS_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "ready": ready,
        "promptReady": prompt_ready,
        "providerReady": provider_ready,
        "providerRuntime": provider_runtime,
        "mode": prompt_readiness.get("mode"),
        "scope": prompt_readiness.get("scope"),
        "operation": prompt_readiness.get("operation"),
        "maxCandidates": prompt_readiness.get("maxCandidates"),
        "minScore": prompt_readiness.get("minScore"),
        "maskThreshold": prompt_readiness.get("maskThreshold"),
        "prompt": prompt_readiness.get("prompt"),
        "blockedReasonCodes": blocked_reason_codes,
        "providerWarningCodes": provider_warning_codes,
    }


def _bound_context_actions_snapshot_payload(
    snapshot: Mapping[str, Any],
    bound_readiness: Mapping[str, Any],
    run_status_state: Mapping[str, Any] | None,
    binding: Sam3ProviderBinding | None = None,
) -> dict[str, Any]:
    return {
        **snapshot,
        "schemaVersion": _BOUND_CONTEXT_ACTIONS_SCHEMA_VERSION,
        "actions": [
            _bound_context_action_payload(
                action,
                bound_readiness,
                run_status_state,
                binding,
            )
            for action in snapshot.get("actions", [])
        ],
    }


def _bound_context_action_payload(
    action: Mapping[str, Any],
    bound_readiness: Mapping[str, Any],
    run_status_state: Mapping[str, Any] | None,
    binding: Sam3ProviderBinding | None,
) -> dict[str, Any]:
    action_id = str(action.get("id") or "")
    if action_id == _RUN_ACTION_ID:
        return _bound_run_context_action_payload(bound_readiness)
    if action_id == _CONTEXT_ACTION_RUN_STATUS:
        return _run_status_context_action_payload(bound_readiness, run_status_state)
    if _is_refine_context_action_id(action_id):
        return _bound_refine_context_action_payload(action, bound_readiness, binding)
    return dict(action)


def _bound_refine_context_action_payload(
    action: Mapping[str, Any],
    bound_readiness: Mapping[str, Any],
    binding: Sam3ProviderBinding | None,
) -> dict[str, Any]:
    payload = dict(action)
    local_blocked = list(payload.get("blockedReasonCodes", []))
    if local_blocked:
        payload["enabled"] = False
        payload["blockedReasonCodes"] = local_blocked
        return payload

    if binding is not None:
        provider_ready = binding.ready
        provider_blocked = list(binding.blocked_reason_codes)
    else:
        provider_ready = bound_readiness.get("providerReady")
        provider_blocked = list(bound_readiness.get("blockedReasonCodes", []))
        if provider_ready is None:
            provider_blocked = ["sam3_provider_not_ready"]

    if provider_ready is True:
        payload["enabled"] = bool(action.get("enabled", False))
        payload["blockedReasonCodes"] = []
        return payload

    if not provider_blocked:
        provider_blocked = ["sam3_provider_not_ready"]
    payload["enabled"] = False
    payload["blockedReasonCodes"] = provider_blocked
    return payload


def _bound_run_context_action_payload(bound_readiness: Mapping[str, Any]) -> dict[str, Any]:
    return _context_action_payload(
        _RUN_ACTION_ID,
        "run",
        _ts("Run"),
        enabled=bool(bound_readiness.get("ready", False)),
        blocked_reason_codes=list(bound_readiness.get("blockedReasonCodes", [])),
    )


def _run_event_payload(
    result: Sam3AiSelectImageRunResult,
    request_id: str,
) -> dict[str, Any]:
    selection_result = result.selection_result
    candidate_set = selection_result.candidate_set
    session = selection_result.session
    return {
        "schemaVersion": result.schema_version,
        "tool": TOOL_ID,
        "requestId": request_id,
        "completed": result.completed,
        "status": result.status,
        "sourceSnapshotId": result.source_snapshot.source_ref.snapshot_id,
        "candidateSetId": candidate_set.set_id if candidate_set is not None else None,
        "selectedCandidateId": session.selected_candidate_id if session is not None else None,
        "blockedReasonCodes": [reason.code for reason in selection_result.blocked_reasons],
    }


def _run_state_payload(result: Sam3AiSelectImageRunResult) -> dict[str, Any]:
    selection_result = result.selection_result
    candidate_set = selection_result.candidate_set
    return {
        "schemaVersion": result.schema_version,
        "requestId": selection_result.request.request_id,
        "completed": result.completed,
        "status": result.status,
        "sourceSnapshotId": result.source_snapshot.source_ref.snapshot_id,
        "candidateSetId": candidate_set.set_id if candidate_set is not None else None,
        "blockedReasonCodes": [reason.code for reason in selection_result.blocked_reasons],
    }


def _run_action_result_payload(
    *,
    status: str,
    run_result: Sam3AiSelectImageRunResult | None = None,
    session: Sam3SelectionSession | None = None,
    overlay_model: Sam3SelectionOverlayModel | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _RUN_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": _RUN_ACTION_ID,
        "status": status,
        "handled": True,
        "run": _run_state_payload(run_result) if run_result is not None else None,
        "session": _session_state_payload(session) if session is not None else None,
        "overlay": (_overlay_state_payload(overlay_model) if overlay_model is not None else None),
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _refine_event_payload(result: Sam3SelectionRefineResult) -> dict[str, Any]:
    return {
        **_refine_result_summary_payload(result),
        "tool": TOOL_ID,
    }


def _refine_result_summary_payload(result: Sam3SelectionRefineResult) -> dict[str, Any]:
    session = result.session
    candidate_set = result.candidate_set
    return {
        "schemaVersion": result.schema_version,
        "status": result.status,
        "completed": result.completed,
        "requestId": result.request.request_id if result.request is not None else None,
        "candidateSetId": candidate_set.set_id if candidate_set is not None else None,
        "selectedCandidateId": (session.selected_candidate_id if session is not None else None),
        "blockedReasonCodes": [reason.code for reason in result.blocked_reasons],
        "warningCodes": [warning.code for warning in result.warnings],
    }


def _refine_action_result_payload(
    *,
    action_id: str = _REFINE_ACTION_ID,
    status: str,
    refine_result: Sam3SelectionRefineResult | None = None,
    session: Sam3SelectionSession | None = None,
    overlay_model: Sam3SelectionOverlayModel | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _REFINE_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": True,
        "targetCandidateId": _refine_target_candidate_id(refine_result),
        "refine": (
            _refine_result_summary_payload(refine_result) if refine_result is not None else None
        ),
        "session": _session_state_payload(session) if session is not None else None,
        "overlay": (_overlay_state_payload(overlay_model) if overlay_model is not None else None),
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _refine_target_candidate_id(refine_result: Sam3SelectionRefineResult | None) -> str | None:
    """Base candidate a refine ran on (from the request's safe metadata)."""

    if refine_result is None or refine_result.request is None:
        return None
    value = refine_result.request.metadata.get("sourceCandidateId")
    text = str(value).strip() if value is not None else ""
    return text or None


def _default_run_request_id() -> str:
    return f"sam3-ai-select-run-{uuid4().hex}"


def _default_refine_request_id() -> str:
    return f"sam3-ai-select-refine-{uuid4().hex}"


def _apply_event_payload(result: Sam3SelectionApplyExecutionResult) -> dict[str, Any]:
    plan = result.plan
    return {
        "schemaVersion": result.schema_version,
        "tool": TOOL_ID,
        "status": result.status,
        "applied": result.applied,
        "submitted": result.submitted,
        "candidateSetId": plan.candidate_set_id,
        "candidateId": plan.candidate_id,
        "operation": plan.operation,
        "commandSource": result.command_source,
        "blockedReasonCodes": [reason.code for reason in result.blocked_reasons],
    }


def _apply_plan_event_payload(plan: Sam3SelectionApplyPlan) -> dict[str, Any]:
    return {
        "schemaVersion": SAM3_SELECTION_APPLY_PLAN_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "status": plan.status,
        "ready": plan.ready,
        "candidateSetId": plan.candidate_set_id,
        "candidateId": plan.candidate_id,
        "operation": plan.operation,
        "selectionBounds": (
            list(plan.selection_bounds) if plan.selection_bounds is not None else None
        ),
        "blockedReasonCodes": [reason.code for reason in plan.blocked_reasons],
        "warningCodes": [warning.code for warning in plan.warnings],
    }


def _session_state_payload(session: Sam3SelectionSession) -> dict[str, Any]:
    return {
        "schemaVersion": session.schema_version,
        "candidateSetId": session.candidate_set.set_id,
        "selectedCandidateId": session.selected_candidate_id,
        "operation": session.operation,
        "availableCandidateIds": list(session.available_candidate_ids),
        "rejectedCandidateIds": list(session.rejected_candidate_ids),
    }


def _overlay_state_payload(overlay_model: Sam3SelectionOverlayModel) -> dict[str, Any]:
    active_item = overlay_model.active_item
    return {
        "schemaVersion": overlay_model.schema_version,
        "activeCandidateId": active_item.candidate_id if active_item is not None else None,
        "visibleCandidateIds": [item.candidate_id for item in overlay_model.visible_items],
        "warningCodes": [warning.code for warning in overlay_model.warnings],
    }


def _manga_mask_assistant_active_preset(preset: object) -> MangaMaskPreset | None:
    if not isinstance(preset, str):
        return None
    preset_key = "_".join(str(preset).strip().lower().replace("-", " ").replace("_", " ").split())
    if preset_key not in SAM3_MANGA_MASK_PRESET_VALUES:
        return None
    return cast(MangaMaskPreset, preset_key)


def _manga_mask_assistant_empty_table_model(
    active_preset: MangaMaskPreset,
    *,
    warning_codes: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "schemaVersion": SAM3_MANGA_MASK_ASSISTANT_TABLE_MODEL_SCHEMA_VERSION,
        "filterResultSchemaVersion": SAM3_MANGA_MASK_ASSISTANT_FILTER_RESULT_SCHEMA_VERSION,
        "activePreset": active_preset,
        "totalRowCount": 0,
        "visibleRowCount": 0,
        "hiddenRowCount": 0,
        "columns": list(SAM3_MANGA_MASK_ASSISTANT_TABLE_COLUMNS),
        "rows": [],
        "warningCodes": list(warning_codes),
    }


def _manga_mask_assistant_warning_codes(exc: BaseException) -> tuple[str, ...]:
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        clean_code = code.strip()
        if clean_code:
            return (clean_code,)
    return ("manga_mask_assistant_table_model_failed",)


def _manga_mask_assistant_package_build_result_payload(
    *,
    status: str = "blocked",
    handled: bool = False,
    package: Mapping[str, Any] | None = None,
    blocked_reason_codes: Sequence[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _MANGA_MASK_ASSISTANT_PACKAGE_BUILD_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "status": status,
        "handled": handled,
        "package": dict(package) if package is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _manga_mask_assistant_package_build_readiness_payload(
    *,
    ready: bool = False,
    has_session: bool = False,
    candidate_set_id: str | None = None,
    candidate_count: int = 0,
    package: Mapping[str, Any] | None = None,
    blocked_reason_codes: Sequence[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _MANGA_MASK_ASSISTANT_PACKAGE_BUILD_READINESS_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "ready": ready,
        "hasSession": has_session,
        "candidateSetId": candidate_set_id,
        "candidateCount": max(0, int(candidate_count)),
        "package": dict(package) if package is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _manga_mask_assistant_candidate_acceptance_result_payload(
    *,
    status: str = "blocked",
    handled: bool = False,
    candidate_id: str | None = None,
    accepted: bool | None = None,
    scope: str | None = None,
    panel_id: str | None = None,
    package: Mapping[str, Any] | None = None,
    blocked_reason_codes: Sequence[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _MANGA_MASK_ASSISTANT_CANDIDATE_ACCEPTANCE_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "status": status,
        "handled": handled,
        "candidateId": candidate_id,
        "accepted": accepted,
        "scope": scope,
        "panelId": panel_id,
        "package": dict(package) if package is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _manga_mask_assistant_scorecard_snapshot_payload(
    *,
    ready: bool = False,
    ok: bool = False,
    package_id: str | None = None,
    candidate_count: int = 0,
    accepted_candidate_count: int = 0,
    ready_candidate_count: int = 0,
    scorecard: Mapping[str, Any] | None = None,
    blocked_reason_codes: Sequence[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _MANGA_MASK_ASSISTANT_SCORECARD_SNAPSHOT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "ready": ready,
        "ok": ok,
        "packageId": package_id,
        "candidateCount": max(0, int(candidate_count)),
        "acceptedCandidateCount": max(0, int(accepted_candidate_count)),
        "readyCandidateCount": max(0, int(ready_candidate_count)),
        "scorecard": dict(scorecard) if scorecard is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _manga_mask_assistant_production_scorecard_snapshot_payload(
    *,
    ready: bool = False,
    ok: bool = False,
    package_id: str | None = None,
    candidate_count: int = 0,
    accepted_candidate_count: int = 0,
    ready_candidate_count: int = 0,
    selected_candidate_id: str | None = None,
    production_projection: Mapping[str, Any] | None = None,
    blocked_reason_codes: Sequence[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _MANGA_MASK_ASSISTANT_PRODUCTION_SCORECARD_SNAPSHOT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "ready": ready,
        "ok": ok,
        "packageId": package_id,
        "candidateCount": max(0, int(candidate_count)),
        "acceptedCandidateCount": max(0, int(accepted_candidate_count)),
        "readyCandidateCount": max(0, int(ready_candidate_count)),
        "selectedCandidateId": selected_candidate_id,
        "productionProjection": (
            dict(production_projection) if production_projection is not None else None
        ),
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _manga_mask_assistant_package_summary(
    package: Sam3ComicMaskPackage,
    *,
    candidate_set_id: str | None,
) -> dict[str, Any]:
    panel_candidates = tuple(
        candidate for panel in package.panel_masks for candidate in panel.candidates
    )
    global_candidates = tuple(package.global_masks)
    candidates = (*panel_candidates, *global_candidates)
    return {
        "packageId": package.package_id,
        "sourceDocumentId": package.source_document_id,
        "sourcePageId": package.source_page_id,
        "candidateSetId": candidate_set_id,
        "panelCount": len(package.panel_masks),
        "panelCandidateCount": len(panel_candidates),
        "globalCandidateCount": len(global_candidates),
        "candidateCount": len(candidates),
        "acceptedCandidateCount": sum(1 for candidate in candidates if candidate.accepted),
        "warningCodes": list(package.warnings),
        "categoryCounts": _manga_mask_assistant_package_category_counts(candidates),
    }


def _manga_mask_assistant_candidate_set_id(
    session: Sam3SelectionSession | None,
) -> str | None:
    candidate_set = getattr(session, "candidate_set", None)
    candidate_set_id = getattr(candidate_set, "set_id", None)
    text = str(candidate_set_id or "").strip()
    return text or None


def _manga_mask_assistant_package_category_counts(
    candidates: Sequence[Any],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        category = str(getattr(candidate, "category", "") or "").strip()
        if not category:
            continue
        counts[category] = counts.get(category, 0) + 1
    return counts


def _manga_mask_assistant_resolve_action_request(
    action_id: str,
    package: Sam3ComicMaskPackage,
    target: Any,
) -> Any:
    try:
        view_model = build_manga_mask_assistant_view_model(package)
        row = next((row for row in view_model.rows if row.row_id == target.row_id), None)
        if row is None:
            return _manga_mask_assistant_action_result_payload(
                action_id,
                action=target.action,
                row_id=target.row_id,
                blocked_reason_codes=("manga_mask_assistant_action_target_missing",),
            )
        return build_manga_mask_assistant_action_request(row, target.action)
    except Exception as exc:  # noqa: BLE001 - keep action result scrubbed.
        logger.debug(
            "SAM3 Manga Mask Assistant action resolution failed: %s",
            type(exc).__name__,
        )
        return _manga_mask_assistant_action_result_payload(
            action_id,
            status="failed",
            action=target.action,
            row_id=target.row_id,
            error_type=type(exc).__name__,
        )


def _manga_mask_assistant_candidate_for_request(
    package: Sam3ComicMaskPackage,
    request: Any,
) -> Any | None:
    candidate_id = str(getattr(request, "candidate_id", "") or "").strip()
    if not candidate_id:
        return None

    scope = str(getattr(request, "scope", "") or "").strip()
    panel_id = str(getattr(request, "panel_id", "") or "").strip()
    candidates = _manga_mask_assistant_request_candidates(package, scope, panel_id)
    return next(
        (candidate for candidate in candidates if candidate.candidate_id == candidate_id),
        None,
    )


def _manga_mask_assistant_request_candidates(
    package: Sam3ComicMaskPackage,
    scope: str,
    panel_id: str,
) -> tuple[Any, ...]:
    if scope == "panel":
        return tuple(
            candidate
            for panel in package.panel_masks
            if not panel_id or panel.panel_id == panel_id
            for candidate in panel.candidates
        )
    if scope == "global":
        return tuple(package.global_masks)
    return (
        *tuple(candidate for panel in package.panel_masks for candidate in panel.candidates),
        *tuple(package.global_masks),
    )


def _manga_mask_assistant_action_result_from_request(
    request: Any,
    *,
    status: str = "blocked",
    handled: bool = False,
    blocked_reason_codes: Sequence[str] = (),
    error_type: str | None = None,
    selection_apply: Mapping[str, Any] | None = None,
    protect_text: Mapping[str, Any] | None = None,
    inpaint_preparation: Mapping[str, Any] | None = None,
    inpaint_preview_request: Mapping[str, Any] | None = None,
    export_package: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _manga_mask_assistant_action_result_payload(
        request.action_id,
        status=status,
        handled=handled,
        action=request.action,
        row_id=request.row_id,
        scope=request.scope,
        package_id=request.package_id,
        source_page_id=request.source_page_id,
        panel_id=request.panel_id,
        candidate_id=request.candidate_id,
        category=request.category,
        blocked_reason_codes=blocked_reason_codes,
        error_type=error_type,
        selection_apply=selection_apply,
        protect_text=protect_text,
        inpaint_preparation=inpaint_preparation,
        inpaint_preview_request=inpaint_preview_request,
        export_package=export_package,
    )


def _manga_mask_assistant_action_result_payload(
    action_id: str,
    *,
    status: str = "blocked",
    handled: bool = False,
    action: str | None = None,
    row_id: str | None = None,
    scope: str | None = None,
    package_id: str | None = None,
    source_page_id: str | None = None,
    panel_id: str | None = None,
    candidate_id: str | None = None,
    category: str | None = None,
    blocked_reason_codes: Sequence[str] = (),
    error_type: str | None = None,
    selection_apply: Mapping[str, Any] | None = None,
    protect_text: Mapping[str, Any] | None = None,
    inpaint_preparation: Mapping[str, Any] | None = None,
    inpaint_preview_request: Mapping[str, Any] | None = None,
    export_package: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _MANGA_MASK_ASSISTANT_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": handled,
        "action": action,
        "rowId": row_id,
        "scope": scope,
        "packageId": package_id,
        "sourcePageId": source_page_id,
        "panelId": panel_id,
        "candidateId": candidate_id,
        "category": category,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
        "selectionApply": dict(selection_apply) if selection_apply is not None else None,
        "protectText": dict(protect_text) if protect_text is not None else None,
        "inpaintPreparation": (
            dict(inpaint_preparation) if inpaint_preparation is not None else None
        ),
        "inpaintPreviewRequest": (
            dict(inpaint_preview_request) if inpaint_preview_request is not None else None
        ),
        "exportPackage": dict(export_package) if export_package is not None else None,
    }


def _manga_mask_assistant_selection_apply_summary(
    plan: Sam3SelectionApplyPlan,
    *,
    applied: bool = False,
    submitted: bool = False,
) -> dict[str, Any]:
    return {
        "candidateId": plan.candidate_id,
        "operation": plan.operation,
        "selectionBounds": (
            list(plan.selection_bounds) if plan.selection_bounds is not None else None
        ),
        "sourceSize": [plan.source_ref.width, plan.source_ref.height],
        "applied": applied,
        "submitted": submitted,
    }


def _manga_mask_assistant_protect_text_summary(
    plan: Any,
    *,
    canvas_size: tuple[int, int],
    protected_asset: Any | None = None,
    saved: bool = False,
) -> dict[str, Any]:
    return {
        "candidateIds": list(plan.candidate_ids),
        "categories": list(plan.categories),
        "sourceMaskCount": len(plan.mask_asset_ids),
        "protectedMaskAssetId": (protected_asset.asset_id if protected_asset is not None else None),
        "protectedMaskBounds": (
            list(protected_asset.bbox) if protected_asset is not None else None
        ),
        "sourceSize": [canvas_size[0], canvas_size[1]],
        "saved": saved,
    }


def _manga_mask_assistant_inpaint_preparation_summary(
    preparation: Sam3MangaInpaintPreparation,
    *,
    inpaint_mask_available: bool,
    protected_mask_available: bool,
) -> dict[str, Any]:
    return {
        "ready": preparation.ready,
        "candidateId": preparation.candidate_id,
        "category": preparation.category,
        "protectedMaskAssetId": preparation.protected_mask_asset_id,
        "inpaintMaskAvailable": inpaint_mask_available,
        "protectedMaskAvailable": protected_mask_available,
        "requiresUserConfirmation": preparation.requires_user_confirmation,
        "blockedReasonCodes": list(preparation.blocked_reason_codes),
        "prepared": preparation.ready,
    }


def _manga_mask_assistant_inpaint_preview_request_summary(
    *,
    prepared: bool,
    candidate_id: str | None,
    effect_layer_id: str | None,
    package_id: str,
    source_page_id: str,
    apply_to_canvas: bool = False,
    persist_overrides: bool = False,
    blocked_reason_codes: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "prepared": prepared,
        "candidateId": candidate_id,
        "effectLayerId": effect_layer_id,
        "packageId": package_id,
        "sourcePageId": source_page_id,
        "applyToCanvas": apply_to_canvas,
        "persistOverrides": persist_overrides,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _manga_mask_assistant_export_package_summary(
    export_result: Mapping[str, object],
) -> dict[str, Any]:
    export = _mapping(export_result.get("export")) or {}
    return {
        "exported": _clean_optional_text(export_result.get("status")) == "exported",
        "packageId": _clean_optional_text(export_result.get("packageId")),
        "assetPayloadCount": _optional_positive_int(export_result.get("assetPayloadCount")) or 0,
        "fileCount": _optional_positive_int(export.get("fileCount")) or 0,
        "assetCount": _optional_positive_int(export.get("assetCount")) or 0,
        "metadataPath": _clean_optional_text(export.get("metadataPath")),
        "blockedReasonCodes": _string_list(export_result.get("blockedReasonCodes")),
    }


def _manga_mask_assistant_changeset_request_summary(
    package: Sam3ComicMaskPackage,
    request: Any,
    candidate: Any,
) -> dict[str, Any]:
    return {
        "prepared": True,
        "previewOnly": True,
        "applyPathAvailable": False,
        "packageId": package.package_id,
        "sourcePageId": package.source_page_id,
        "scope": request.scope,
        "panelId": request.panel_id,
        "candidateId": request.candidate_id,
        "category": request.category,
        "operationCount": 1,
        "operationKinds": ["manga_mask_candidate_to_editable_mask"],
        "candidateBounds": _manga_mask_assistant_bounds_list(candidate.bbox),
        "panelLocalBounds": _manga_mask_assistant_optional_bounds_list(
            getattr(candidate, "panel_local_bbox", None)
        ),
        "blockedReasonCodes": [],
    }


def _manga_mask_assistant_optional_bounds_list(value: object) -> list[int] | None:
    if value is None:
        return None
    return _manga_mask_assistant_bounds_list(value)


def _manga_mask_assistant_bounds_list(value: object) -> list[int]:
    if not isinstance(value, Iterable) or isinstance(value, (bytes, str)):
        raise ValueError("manga_mask_changeset_bounds_invalid")
    items = tuple(value)
    if len(items) != 4:
        raise ValueError("manga_mask_changeset_bounds_invalid")
    return [int(item) for item in items]


def _manga_mask_assistant_preparation_with_asset_readiness(
    preparation: Sam3MangaInpaintPreparation,
    *,
    inpaint_mask_available: bool,
) -> Sam3MangaInpaintPreparation:
    if inpaint_mask_available:
        return preparation

    blocked_reasons: list[str] = []
    inserted_mask_missing = False
    for code in preparation.blocked_reason_codes:
        if code == "manga_inpaint_protected_mask_missing" and not inserted_mask_missing:
            blocked_reasons.append("manga_inpaint_mask_asset_missing")
            inserted_mask_missing = True
        blocked_reasons.append(code)
    if not inserted_mask_missing:
        blocked_reasons.append("manga_inpaint_mask_asset_missing")
    return replace(
        preparation,
        ready=False,
        blocked_reason_codes=tuple(dict.fromkeys(blocked_reasons)),
    )


def _candidate_list_snapshot_payload(
    overlay_model: Sam3SelectionOverlayModel | None,
) -> dict[str, Any]:
    if overlay_model is None:
        return {
            "schemaVersion": _CANDIDATE_LIST_SCHEMA_VERSION,
            "tool": TOOL_ID,
            "hasOverlayModel": False,
            "candidateSetId": None,
            "selectedCandidateId": None,
            "availableCandidateIds": [],
            "rejectedCandidateIds": [],
            "visibleCandidateIds": [],
            "items": [],
            "warningCodes": [],
        }

    return {
        "schemaVersion": _CANDIDATE_LIST_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "hasOverlayModel": True,
        "candidateSetId": overlay_model.candidate_set_id,
        "selectedCandidateId": overlay_model.selected_candidate_id,
        "availableCandidateIds": list(overlay_model.available_candidate_ids),
        "rejectedCandidateIds": list(overlay_model.rejected_candidate_ids),
        "visibleCandidateIds": [item.candidate_id for item in overlay_model.visible_items],
        "items": [_candidate_list_item_payload(item) for item in overlay_model.items],
        "warningCodes": [warning.code for warning in overlay_model.warnings],
    }


def _candidate_list_item_payload(item: Sam3CandidateOverlayItem) -> dict[str, Any]:
    return {
        "candidateId": item.candidate_id,
        "label": item.label,
        "score": item.score,
        "rank": item.rank,
        "bbox": list(item.bbox),
        "clippedBbox": list(item.clipped_bbox) if item.clipped_bbox is not None else None,
        "areaPx": item.area_px,
        "instanceIndex": item.instance_index,
        "groupId": item.group_id,
        "state": item.state,
        "visible": item.visible,
        "style": item.style.to_dict(),
    }


def _context_actions_snapshot_payload(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
    history_store: object | None,
    run_status_state: Mapping[str, Any] | None,
    settings: Sam3AiSelectToolSettings,
    *,
    candidate_overlay_visible: bool = True,
) -> dict[str, Any]:
    if session is None:
        return {
            "schemaVersion": _CONTEXT_ACTIONS_SCHEMA_VERSION,
            "tool": TOOL_ID,
            "hasSession": False,
            "candidateSetId": None,
            "selectedCandidateId": None,
            "operation": None,
            "availableCandidateIds": [],
            "selectedCandidateIndex": None,
            "applyPlan": None,
            "actions": _context_action_payloads(
                None,
                None,
                None,
                asset_store,
                history_store,
                run_status_state,
                settings,
                candidate_overlay_visible=candidate_overlay_visible,
            ),
        }

    available_candidate_ids = list(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    selected_candidate_index = (
        available_candidate_ids.index(selected_candidate_id)
        if selected_candidate_id in available_candidate_ids
        else None
    )
    apply_plan = build_session_apply_plan(session)
    return {
        "schemaVersion": _CONTEXT_ACTIONS_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "hasSession": True,
        "candidateSetId": session.candidate_set.set_id,
        "selectedCandidateId": selected_candidate_id,
        "operation": session.operation,
        "availableCandidateIds": available_candidate_ids,
        "selectedCandidateIndex": selected_candidate_index,
        "applyPlan": _apply_plan_event_payload(apply_plan),
        "actions": _context_action_payloads(
            session,
            overlay_model,
            apply_plan,
            asset_store,
            history_store,
            run_status_state,
            settings,
            candidate_overlay_visible=candidate_overlay_visible,
        ),
    }


def _context_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    apply_plan: Sam3SelectionApplyPlan | None,
    asset_store: Sam3AssetStore | None,
    history_store: object | None,
    run_status_state: Mapping[str, Any] | None,
    settings: Sam3AiSelectToolSettings,
    *,
    candidate_overlay_visible: bool = True,
) -> list[dict[str, Any]]:
    run_action = _run_context_action_payload(settings)
    run_status_action = _context_run_status_action_payload(settings, run_status_state)
    status_action = _context_status_action_payload(session)
    summary_action = _context_summary_action_payload(overlay_model)
    candidate_select_actions = _context_candidate_select_action_payloads(
        session,
        overlay_model,
    )
    candidate_reject_actions = _context_candidate_reject_action_payloads(
        session,
        overlay_model,
    )
    candidate_refine_actions = _context_candidate_refine_action_payloads(
        session,
        overlay_model,
    )
    candidate_layer_mask_actions = _context_candidate_layer_mask_action_payloads(
        session,
        overlay_model,
        asset_store,
    )
    candidate_background_generation_mask_actions = (
        _context_candidate_background_generation_mask_action_payloads(
            session,
            overlay_model,
            asset_store,
        )
    )
    candidate_inpaint_mask_actions = _context_candidate_inpaint_mask_action_payloads(
        session,
        overlay_model,
        asset_store,
    )
    candidate_inpaint_preview_actions = _context_candidate_inpaint_preview_action_payloads(
        session,
        overlay_model,
        asset_store,
    )
    candidate_clipping_mask_actions = _context_candidate_clipping_mask_action_payloads(
        session,
        overlay_model,
        asset_store,
    )
    candidate_cut_to_layer_actions = _context_candidate_cut_to_layer_action_payloads(
        session,
        overlay_model,
        asset_store,
    )
    candidate_extract_foreground_actions = _context_candidate_extract_to_layer_action_payloads(
        session,
        overlay_model,
        asset_store,
        mode="foreground",
    )
    candidate_extract_background_actions = _context_candidate_extract_to_layer_action_payloads(
        session,
        overlay_model,
        asset_store,
        mode="background",
    )
    candidate_save_mask_asset_actions = _context_candidate_save_mask_asset_action_payloads(
        session,
        overlay_model,
        asset_store,
        history_store,
    )
    if session is None:
        blocked = ["ai_select_session_missing"]
        return [
            run_action,
            run_status_action,
            status_action,
            summary_action,
            _context_action_payload(
                _CONTEXT_ACTION_PREVIOUS,
                "candidate_navigation",
                _ts("Previous"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_NEXT,
                "candidate_navigation",
                _ts("Next"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_REJECT,
                "candidate_reject",
                _ts("Reject"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _REFINE_ACTION_ID,
                "refine",
                _ts("AI Refine..."),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_APPLY_CURRENT,
                "apply",
                _ts("Apply"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_APPLY_CURRENT_AS_LAYER_MASK,
                "apply_layer_mask",
                _ts("Apply as Layer Mask"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_USE_CURRENT_AS_INPAINT_MASK,
                "use_as_inpaint_mask",
                _ts("Use as Inpaint Mask"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_PREVIEW_CURRENT_INPAINT,
                "prepare_inpaint_preview_request",
                _ts("Preview"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_USE_SELECTION_AS_INPAINT_MASK,
                "use_selection_as_inpaint_mask",
                _ts("Use Selection as Inpaint Mask"),
                enabled=True,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT,
                "prepare_selection_inpaint_preview_request",
                _ts("Preview Selection Inpaint"),
                enabled=True,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_ADD_CURRENT_TO_LAYER_MASK,
                "add_candidate_to_existing_layer_mask",
                _ts("Add Candidate to Existing Mask"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_TOGGLE_CANDIDATE_OVERLAY,
                "toggle_candidate_overlay",
                _ts("Toggle Candidate Overlay"),
                blocked_reason_codes=["ai_select_overlay_missing"],
            ),
            _context_action_payload(
                _CONTEXT_ACTION_APPLY_CURRENT_AS_CLIPPING_MASK,
                "apply_clipping_mask",
                _ts("Apply as Clipping Mask"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_APPLY_CURRENT_TO_NEW_LAYER,
                "apply_cut_to_new_layer",
                _ts("Cut to New Layer"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_EXTRACT_CURRENT_FOREGROUND,
                "extract_foreground_to_new_layer",
                _ts("Extract Foreground"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_EXTRACT_CURRENT_BACKGROUND,
                "extract_background_to_new_layer",
                _ts("Extract Background"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_SAVE_CURRENT_MASK_ASSET,
                "save_mask_asset",
                _ts("Save Mask Asset"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_EXPORT_CURRENT_CANDIDATE,
                "export_current_candidate",
                _ts("Export PNG..."),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_EXPORT_ALL_CANDIDATES,
                "export_all_candidates",
                _ts("Export All..."),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_EXPORT_SPRITE_SHEET,
                "export_sprite_sheet",
                _ts("Export Sheet..."),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_EXPORT_SELECTION,
                "export_selection",
                _ts("Export Selection..."),
                enabled=True,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_COMBINE_SELECTED,
                "combine_selected",
                _ts("Combine Selected"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_GROUP_SELECTED_TO_LAYER_GROUP,
                "candidate_group_to_layer_group",
                _ts("Layer Group"),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_MASK_REFINE_SELECTED,
                "mask_refine_selected",
                _ts("Refine Mask..."),
                blocked_reason_codes=blocked,
            ),
            _context_action_payload(
                _CONTEXT_ACTION_USE_CURRENT_AS_BACKGROUND_GENERATION_MASK,
                "use_as_background_generation_mask",
                _ts("Protect Subject (BG Mask)"),
                blocked_reason_codes=blocked,
            ),
            *[
                _context_action_payload(
                    f"sam3_ai_select.operation.{operation}",
                    "operation",
                    _operation_label(operation),
                    operation=operation,
                    blocked_reason_codes=blocked,
                )
                for operation in SAM3_AI_SELECT_OPERATION_VALUES
            ],
            *[
                _context_action_payload(
                    action_id,
                    "apply",
                    _apply_operation_action_label(operation),
                    operation=operation,
                    blocked_reason_codes=blocked,
                )
                for action_id, operation in _CONTEXT_APPLY_OPERATION_BY_ACTION_ID.items()
            ],
        ]

    navigation_blocked = (
        [] if len(session.available_candidate_ids) > 1 else ["candidate_navigation_unavailable"]
    )
    reject_blocked = (
        [] if session.selected_candidate_id is not None else ["candidate_selection_missing"]
    )
    refine_blocked = _context_refine_blocked_reason_codes(session)
    apply_blocked = _context_apply_blocked_reason_codes(apply_plan, asset_store)
    layer_mask_apply_blocked = _context_layer_mask_apply_blocked_reason_codes(
        session,
        asset_store,
    )
    ai_effect_mask_slot_blocked = list(layer_mask_apply_blocked)
    overlay_toggle_blocked = [] if overlay_model is not None else ["ai_select_overlay_missing"]
    add_to_layer_mask_blocked = list(layer_mask_apply_blocked)
    clipping_mask_apply_blocked = list(layer_mask_apply_blocked)
    cut_to_layer_blocked = list(layer_mask_apply_blocked)
    extract_to_layer_blocked = list(layer_mask_apply_blocked)
    save_mask_asset_blocked = _context_save_mask_asset_blocked_reason_codes(
        session,
        asset_store,
        history_store,
    )
    combine_selected_blocked = _context_combine_selected_blocked_reason_codes(
        session,
        asset_store,
    )
    candidate_group_to_layer_group_blocked = (
        _context_candidate_group_to_layer_group_blocked_reason_codes(
            session,
            asset_store,
        )
    )
    mask_refine_selected_blocked = _context_mask_refine_blocked_reason_codes(
        session,
        asset_store,
    )
    return [
        run_action,
        run_status_action,
        status_action,
        summary_action,
        _context_action_payload(
            _CONTEXT_ACTION_PREVIOUS,
            "candidate_navigation",
            _ts("Previous"),
            enabled=not navigation_blocked,
            blocked_reason_codes=navigation_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_NEXT,
            "candidate_navigation",
            _ts("Next"),
            enabled=not navigation_blocked,
            blocked_reason_codes=navigation_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_REJECT,
            "candidate_reject",
            _ts("Reject"),
            enabled=not reject_blocked,
            blocked_reason_codes=reject_blocked,
        ),
        _context_action_payload(
            _REFINE_ACTION_ID,
            "refine",
            _ts("AI Refine..."),
            enabled=not refine_blocked,
            blocked_reason_codes=refine_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_APPLY_CURRENT,
            "apply",
            _ts("Apply"),
            enabled=not apply_blocked,
            blocked_reason_codes=apply_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_APPLY_CURRENT_AS_LAYER_MASK,
            "apply_layer_mask",
            _ts("Apply as Layer Mask"),
            enabled=not layer_mask_apply_blocked,
            blocked_reason_codes=layer_mask_apply_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_USE_CURRENT_AS_INPAINT_MASK,
            "use_as_inpaint_mask",
            _ts("Use as Inpaint Mask"),
            enabled=not ai_effect_mask_slot_blocked,
            blocked_reason_codes=ai_effect_mask_slot_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_PREVIEW_CURRENT_INPAINT,
            "prepare_inpaint_preview_request",
            _ts("Preview"),
            enabled=not ai_effect_mask_slot_blocked,
            blocked_reason_codes=ai_effect_mask_slot_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_USE_SELECTION_AS_INPAINT_MASK,
            "use_selection_as_inpaint_mask",
            _ts("Use Selection as Inpaint Mask"),
            enabled=True,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT,
            "prepare_selection_inpaint_preview_request",
            _ts("Preview Selection Inpaint"),
            enabled=True,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_ADD_CURRENT_TO_LAYER_MASK,
            "add_candidate_to_existing_layer_mask",
            _ts("Add Candidate to Existing Mask"),
            enabled=not add_to_layer_mask_blocked,
            blocked_reason_codes=add_to_layer_mask_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_TOGGLE_CANDIDATE_OVERLAY,
            "toggle_candidate_overlay",
            _ts("Toggle Candidate Overlay"),
            enabled=not overlay_toggle_blocked,
            checked=bool(candidate_overlay_visible and overlay_model is not None),
            blocked_reason_codes=overlay_toggle_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_APPLY_CURRENT_AS_CLIPPING_MASK,
            "apply_clipping_mask",
            _ts("Apply as Clipping Mask"),
            enabled=not clipping_mask_apply_blocked,
            blocked_reason_codes=clipping_mask_apply_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_APPLY_CURRENT_TO_NEW_LAYER,
            "apply_cut_to_new_layer",
            _ts("Cut to New Layer"),
            enabled=not cut_to_layer_blocked,
            blocked_reason_codes=cut_to_layer_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_EXTRACT_CURRENT_FOREGROUND,
            "extract_foreground_to_new_layer",
            _ts("Extract Foreground"),
            enabled=not extract_to_layer_blocked,
            blocked_reason_codes=extract_to_layer_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_EXTRACT_CURRENT_BACKGROUND,
            "extract_background_to_new_layer",
            _ts("Extract Background"),
            enabled=not extract_to_layer_blocked,
            blocked_reason_codes=extract_to_layer_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_SAVE_CURRENT_MASK_ASSET,
            "save_mask_asset",
            _ts("Save Mask Asset"),
            enabled=not save_mask_asset_blocked,
            blocked_reason_codes=save_mask_asset_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_EXPORT_CURRENT_CANDIDATE,
            "export_current_candidate",
            _ts("Export PNG..."),
            enabled=not cut_to_layer_blocked,
            blocked_reason_codes=cut_to_layer_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_EXPORT_ALL_CANDIDATES,
            "export_all_candidates",
            _ts("Export All..."),
            # Session-level gate only: an all-rejected set stays clickable and
            # reports asset_export_candidates_missing from the handler.
            enabled=True,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_EXPORT_SPRITE_SHEET,
            "export_sprite_sheet",
            _ts("Export Sheet..."),
            # Same session-level gate as Export All (handler reports the
            # candidates_missing / empty / too_large specifics).
            enabled=True,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_EXPORT_SELECTION,
            "export_selection",
            _ts("Export Selection..."),
            enabled=True,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_COMBINE_SELECTED,
            "combine_selected",
            _ts("Combine Selected"),
            enabled=not combine_selected_blocked,
            blocked_reason_codes=combine_selected_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_GROUP_SELECTED_TO_LAYER_GROUP,
            "candidate_group_to_layer_group",
            _ts("Layer Group"),
            enabled=not candidate_group_to_layer_group_blocked,
            blocked_reason_codes=candidate_group_to_layer_group_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_MASK_REFINE_SELECTED,
            "mask_refine_selected",
            _ts("Refine Mask..."),
            enabled=not mask_refine_selected_blocked,
            blocked_reason_codes=mask_refine_selected_blocked,
        ),
        _context_action_payload(
            _CONTEXT_ACTION_USE_CURRENT_AS_BACKGROUND_GENERATION_MASK,
            "use_as_background_generation_mask",
            _ts("Protect Subject (BG Mask)"),
            enabled=not ai_effect_mask_slot_blocked,
            blocked_reason_codes=ai_effect_mask_slot_blocked,
        ),
        *[
            _context_action_payload(
                f"sam3_ai_select.operation.{operation}",
                "operation",
                _operation_label(operation),
                enabled=True,
                checked=session.operation == operation,
                operation=operation,
            )
            for operation in SAM3_AI_SELECT_OPERATION_VALUES
        ],
        *[
            _context_action_payload(
                action_id,
                "apply",
                _apply_operation_action_label(operation),
                enabled=not apply_blocked,
                operation=operation,
                blocked_reason_codes=apply_blocked,
            )
            for action_id, operation in _CONTEXT_APPLY_OPERATION_BY_ACTION_ID.items()
        ],
        *candidate_select_actions,
        *candidate_reject_actions,
        *candidate_refine_actions,
        *candidate_layer_mask_actions,
        *candidate_inpaint_mask_actions,
        *candidate_background_generation_mask_actions,
        *candidate_inpaint_preview_actions,
        *candidate_clipping_mask_actions,
        *candidate_cut_to_layer_actions,
        *candidate_extract_foreground_actions,
        *candidate_extract_background_actions,
        *candidate_save_mask_asset_actions,
    ]


def _context_candidate_select_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        available = candidate_id in available_candidate_ids
        selected = available and candidate_id == selected_candidate_id
        blocked_reason_codes = [] if available else ["candidate_unavailable"]
        actions.append(
            _context_action_payload(
                _candidate_select_action_id(candidate_id),
                "candidate_select",
                _candidate_select_label(item),
                enabled=available,
                checked=selected,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=selected,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_reject_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    rejected_candidate_ids = set(session.rejected_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        rejected = candidate_id in rejected_candidate_ids
        blocked_reason_codes = ["candidate_unavailable"] if rejected else []
        actions.append(
            _context_action_payload(
                _candidate_reject_action_id(candidate_id),
                "candidate_reject",
                _ts("Reject"),
                enabled=not rejected,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=candidate_id == selected_candidate_id,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_refine_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    candidates_by_id = {
        str(candidate.candidate_id).strip(): candidate
        for candidate in session.candidate_set.candidates
        if str(candidate.candidate_id).strip()
    }
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        candidate = candidates_by_id.get(candidate_id)
        available = candidate_id in available_candidate_ids
        if not available:
            blocked_reason_codes = ["candidate_unavailable"]
        elif candidate is None or not str(candidate.mask_asset_id or "").strip():
            blocked_reason_codes = ["candidate_mask_missing"]
        else:
            blocked_reason_codes = []
        actions.append(
            _context_action_payload(
                _candidate_refine_action_id(candidate_id),
                "candidate_refine",
                _ts("Refine"),
                enabled=not blocked_reason_codes,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=candidate_id == selected_candidate_id,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_layer_mask_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    candidates_by_id = {
        str(candidate.candidate_id).strip(): candidate
        for candidate in session.candidate_set.candidates
        if str(candidate.candidate_id).strip()
    }
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        candidate = candidates_by_id.get(candidate_id)
        available = candidate_id in available_candidate_ids
        if not available:
            blocked_reason_codes = ["candidate_unavailable"]
        elif asset_store is None:
            blocked_reason_codes = ["ai_select_asset_store_missing"]
        elif candidate is None or not str(candidate.mask_asset_id or "").strip():
            blocked_reason_codes = ["candidate_mask_missing"]
        else:
            blocked_reason_codes = []
        actions.append(
            _context_action_payload(
                _candidate_apply_layer_mask_action_id(candidate_id),
                "candidate_apply_layer_mask",
                _ts("Mask"),
                enabled=not blocked_reason_codes,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=candidate_id == selected_candidate_id,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_inpaint_mask_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
) -> list[dict[str, Any]]:
    return _context_candidate_mask_slot_action_payloads(
        session,
        overlay_model,
        asset_store,
        action_id_builder=_candidate_use_inpaint_mask_action_id,
        kind="candidate_use_as_inpaint_mask",
        label=_ts("Inpaint"),
    )


def _context_candidate_background_generation_mask_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
) -> list[dict[str, Any]]:
    return _context_candidate_mask_slot_action_payloads(
        session,
        overlay_model,
        asset_store,
        action_id_builder=_candidate_use_background_generation_mask_action_id,
        kind="candidate_use_as_background_generation_mask",
        label=_ts("Protect"),
    )


def _context_candidate_mask_slot_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
    *,
    action_id_builder: Callable[[str], str],
    kind: str,
    label: str,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    candidates_by_id = {
        str(candidate.candidate_id).strip(): candidate
        for candidate in session.candidate_set.candidates
        if str(candidate.candidate_id).strip()
    }
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        candidate = candidates_by_id.get(candidate_id)
        available = candidate_id in available_candidate_ids
        if not available:
            blocked_reason_codes = ["candidate_unavailable"]
        elif asset_store is None:
            blocked_reason_codes = ["ai_select_asset_store_missing"]
        elif candidate is None or not str(candidate.mask_asset_id or "").strip():
            blocked_reason_codes = ["candidate_mask_missing"]
        else:
            blocked_reason_codes = []
        actions.append(
            _context_action_payload(
                action_id_builder(candidate_id),
                kind,
                label,
                enabled=not blocked_reason_codes,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=candidate_id == selected_candidate_id,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_inpaint_preview_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    candidates_by_id = {
        str(candidate.candidate_id).strip(): candidate
        for candidate in session.candidate_set.candidates
        if str(candidate.candidate_id).strip()
    }
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        candidate = candidates_by_id.get(candidate_id)
        available = candidate_id in available_candidate_ids
        selected = candidate_id == selected_candidate_id
        if not available:
            blocked_reason_codes = ["candidate_unavailable"]
        elif asset_store is None:
            blocked_reason_codes = ["ai_select_asset_store_missing"]
        elif candidate is None or not str(candidate.mask_asset_id or "").strip():
            blocked_reason_codes = ["candidate_mask_missing"]
        elif not selected:
            blocked_reason_codes = ["candidate_preview_requires_selected_candidate"]
        else:
            blocked_reason_codes = []
        actions.append(
            _context_action_payload(
                _candidate_preview_inpaint_action_id(candidate_id),
                "candidate_prepare_inpaint_preview_request",
                _ts("Preview"),
                enabled=not blocked_reason_codes,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=selected,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_clipping_mask_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    candidates_by_id = {
        str(candidate.candidate_id).strip(): candidate
        for candidate in session.candidate_set.candidates
        if str(candidate.candidate_id).strip()
    }
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        candidate = candidates_by_id.get(candidate_id)
        available = candidate_id in available_candidate_ids
        if not available:
            blocked_reason_codes = ["candidate_unavailable"]
        elif asset_store is None:
            blocked_reason_codes = ["ai_select_asset_store_missing"]
        elif candidate is None or not str(candidate.mask_asset_id or "").strip():
            blocked_reason_codes = ["candidate_mask_missing"]
        else:
            blocked_reason_codes = []
        actions.append(
            _context_action_payload(
                _candidate_apply_clipping_mask_action_id(candidate_id),
                "candidate_apply_clipping_mask",
                _ts("Clip"),
                enabled=not blocked_reason_codes,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=candidate_id == selected_candidate_id,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_cut_to_layer_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    candidates_by_id = {
        str(candidate.candidate_id).strip(): candidate
        for candidate in session.candidate_set.candidates
        if str(candidate.candidate_id).strip()
    }
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        candidate = candidates_by_id.get(candidate_id)
        available = candidate_id in available_candidate_ids
        if not available:
            blocked_reason_codes = ["candidate_unavailable"]
        elif asset_store is None:
            blocked_reason_codes = ["ai_select_asset_store_missing"]
        elif candidate is None or not str(candidate.mask_asset_id or "").strip():
            blocked_reason_codes = ["candidate_mask_missing"]
        else:
            blocked_reason_codes = []
        actions.append(
            _context_action_payload(
                _candidate_cut_to_layer_action_id(candidate_id),
                "candidate_cut_to_new_layer",
                _ts("Cut"),
                enabled=not blocked_reason_codes,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=candidate_id == selected_candidate_id,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_extract_to_layer_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
    *,
    mode: Sam3ExtractLayerMode,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    candidates_by_id = {
        str(candidate.candidate_id).strip(): candidate
        for candidate in session.candidate_set.candidates
        if str(candidate.candidate_id).strip()
    }
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        candidate = candidates_by_id.get(candidate_id)
        available = candidate_id in available_candidate_ids
        if not available:
            blocked_reason_codes = ["candidate_unavailable"]
        elif asset_store is None:
            blocked_reason_codes = ["ai_select_asset_store_missing"]
        elif candidate is None or not str(candidate.mask_asset_id or "").strip():
            blocked_reason_codes = ["candidate_mask_missing"]
        else:
            blocked_reason_codes = []
        actions.append(
            _context_action_payload(
                _candidate_extract_to_layer_action_id(candidate_id, mode=mode),
                _candidate_extract_to_layer_kind(mode),
                _candidate_extract_to_layer_label(mode),
                enabled=not blocked_reason_codes,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=candidate_id == selected_candidate_id,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _context_candidate_save_mask_asset_action_payloads(
    session: Sam3SelectionSession | None,
    overlay_model: Sam3SelectionOverlayModel | None,
    asset_store: Sam3AssetStore | None,
    history_store: object | None,
) -> list[dict[str, Any]]:
    if session is None or overlay_model is None:
        return []
    available_candidate_ids = set(session.available_candidate_ids)
    selected_candidate_id = session.selected_candidate_id
    candidates_by_id = {
        str(candidate.candidate_id).strip(): candidate
        for candidate in session.candidate_set.candidates
        if str(candidate.candidate_id).strip()
    }
    actions: list[dict[str, Any]] = []
    for item in overlay_model.items:
        candidate_id = str(item.candidate_id).strip()
        if not candidate_id:
            continue
        candidate = candidates_by_id.get(candidate_id)
        available = candidate_id in available_candidate_ids
        if not available:
            blocked_reason_codes = ["candidate_unavailable"]
        elif asset_store is None:
            blocked_reason_codes = ["ai_select_asset_store_missing"]
        elif candidate is None or not str(candidate.mask_asset_id or "").strip():
            blocked_reason_codes = ["candidate_mask_missing"]
        elif history_store is None:
            blocked_reason_codes = ["generation_history_store_missing"]
        elif not callable(getattr(history_store, "add_record", None)):
            blocked_reason_codes = ["generation_history_store_unavailable"]
        else:
            blocked_reason_codes = []
        actions.append(
            _context_action_payload(
                _candidate_save_mask_asset_action_id(candidate_id),
                "candidate_save_mask_asset",
                _ts("Save"),
                enabled=not blocked_reason_codes,
                blocked_reason_codes=blocked_reason_codes,
                candidate_id=candidate_id,
                selected=candidate_id == selected_candidate_id,
                visible=bool(item.visible),
                rank=item.rank,
                text=_candidate_summary_text(item),
            )
        )
    return actions


def _run_context_action_payload(settings: Sam3AiSelectToolSettings) -> dict[str, Any]:
    readiness = _run_readiness_snapshot_payload(settings)
    return _context_action_payload(
        _RUN_ACTION_ID,
        "run",
        _ts("Run"),
        enabled=bool(readiness["ready"]),
        blocked_reason_codes=list(readiness["blockedReasonCodes"]),
    )


def _context_run_status_action_payload(
    settings: Sam3AiSelectToolSettings,
    run_status_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    readiness = _run_readiness_snapshot_payload(settings)
    return _run_status_context_action_payload(readiness, run_status_state)


def _run_status_context_action_payload(
    readiness: Mapping[str, Any],
    run_status_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    text = _run_status_text(readiness, run_status_state)
    return _context_action_payload(
        _CONTEXT_ACTION_RUN_STATUS,
        "run_status",
        text,
        enabled=True,
        text=text,
    )


def _context_status_action_payload(session: Sam3SelectionSession | None) -> dict[str, Any]:
    available_candidate_ids = list(session.available_candidate_ids) if session is not None else []
    selected_candidate_id = session.selected_candidate_id if session is not None else None
    selected_index = (
        available_candidate_ids.index(selected_candidate_id)
        if selected_candidate_id in available_candidate_ids
        else None
    )
    text = _candidate_status_text(selected_index, len(available_candidate_ids))
    return _context_action_payload(
        _CONTEXT_ACTION_STATUS,
        "candidate_status",
        text,
        enabled=True,
        text=text,
    )


def _candidate_status_text(selected_index: int | None, available_count: int) -> str:
    count = max(0, int(available_count))
    if count <= 0:
        return "0/0"
    if selected_index is None:
        return f"0/{count}"
    return f"{int(selected_index) + 1}/{count}"


def _context_summary_action_payload(
    overlay_model: Sam3SelectionOverlayModel | None,
) -> dict[str, Any]:
    text = _candidate_summary_text(overlay_model.active_item if overlay_model is not None else None)
    return _context_action_payload(
        _CONTEXT_ACTION_SUMMARY,
        "candidate_summary",
        text,
        enabled=True,
        text=text,
    )


def _candidate_summary_text(item: Sam3CandidateOverlayItem | None) -> str:
    if item is None:
        return _ts("No candidate")
    label = str(item.label).strip()
    score_text = _candidate_score_text(item.score)
    if label and score_text is not None:
        return f"{label} {score_text}"
    if label:
        return label
    base = f"{_ts('Candidate')} {_candidate_rank_text(item.rank)}"
    if score_text is not None:
        return f"{base} {score_text}"
    return base


def _candidate_score_text(score: float | None) -> str | None:
    if score is None:
        return None
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    numeric = max(0.0, min(1.0, numeric))
    return f"{round(numeric * 100)}%"


def _candidate_rank_text(rank: int) -> str:
    try:
        value = int(rank)
    except (TypeError, ValueError):
        return "?"
    if value <= 0:
        return "?"
    return str(value)


def _candidate_select_label(item: Sam3CandidateOverlayItem) -> str:
    label = str(item.label).strip()
    if label:
        return label
    return f"{_ts('Candidate')} {_candidate_rank_text(item.rank)}"


def _candidate_select_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_SELECT_ACTION_PREFIX, candidate_id)


def _candidate_id_from_select_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(_CONTEXT_CANDIDATE_SELECT_ACTION_PREFIX, action_id)


def _candidate_reject_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_REJECT_ACTION_PREFIX, candidate_id)


def _candidate_id_from_reject_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(_CONTEXT_CANDIDATE_REJECT_ACTION_PREFIX, action_id)


def _candidate_refine_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_REFINE_ACTION_PREFIX, candidate_id)


def _candidate_id_from_refine_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(_CONTEXT_CANDIDATE_REFINE_ACTION_PREFIX, action_id)


def _candidate_apply_layer_mask_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_APPLY_LAYER_MASK_ACTION_PREFIX, candidate_id)


def _candidate_id_from_apply_layer_mask_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_APPLY_LAYER_MASK_ACTION_PREFIX,
        action_id,
    )


def _candidate_use_inpaint_mask_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_USE_INPAINT_MASK_ACTION_PREFIX, candidate_id)


def _candidate_id_from_use_inpaint_mask_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_USE_INPAINT_MASK_ACTION_PREFIX,
        action_id,
    )


def _candidate_use_background_generation_mask_action_id(candidate_id: str) -> str:
    return _candidate_action_id(
        _CONTEXT_CANDIDATE_USE_BACKGROUND_GENERATION_MASK_ACTION_PREFIX,
        candidate_id,
    )


def _candidate_id_from_use_background_generation_mask_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_USE_BACKGROUND_GENERATION_MASK_ACTION_PREFIX,
        action_id,
    )


def _candidate_preview_inpaint_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_PREVIEW_INPAINT_ACTION_PREFIX, candidate_id)


def _candidate_id_from_preview_inpaint_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_PREVIEW_INPAINT_ACTION_PREFIX,
        action_id,
    )


def _candidate_apply_clipping_mask_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_APPLY_CLIPPING_MASK_ACTION_PREFIX, candidate_id)


def _candidate_id_from_apply_clipping_mask_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_APPLY_CLIPPING_MASK_ACTION_PREFIX,
        action_id,
    )


def _candidate_cut_to_layer_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_CUT_TO_LAYER_ACTION_PREFIX, candidate_id)


def _candidate_id_from_cut_to_layer_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_CUT_TO_LAYER_ACTION_PREFIX,
        action_id,
    )


def _candidate_extract_to_layer_action_id(
    candidate_id: str,
    *,
    mode: Sam3ExtractLayerMode,
) -> str:
    prefix = (
        _CONTEXT_CANDIDATE_EXTRACT_BACKGROUND_ACTION_PREFIX
        if mode == "background"
        else _CONTEXT_CANDIDATE_EXTRACT_FOREGROUND_ACTION_PREFIX
    )
    return _candidate_action_id(prefix, candidate_id)


def _candidate_id_from_extract_to_layer_action_id(action_id: str) -> str | None:
    candidate_id = _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_EXTRACT_FOREGROUND_ACTION_PREFIX,
        action_id,
    )
    if candidate_id is not None:
        return candidate_id
    return _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_EXTRACT_BACKGROUND_ACTION_PREFIX,
        action_id,
    )


def _candidate_save_mask_asset_action_id(candidate_id: str) -> str:
    return _candidate_action_id(_CONTEXT_CANDIDATE_SAVE_MASK_ASSET_ACTION_PREFIX, candidate_id)


def _candidate_id_from_save_mask_asset_action_id(action_id: str) -> str | None:
    return _candidate_id_from_action_id(
        _CONTEXT_CANDIDATE_SAVE_MASK_ASSET_ACTION_PREFIX,
        action_id,
    )


def _candidate_extract_to_layer_kind(mode: Sam3ExtractLayerMode) -> str:
    if mode == "background":
        return "candidate_extract_background_to_new_layer"
    return "candidate_extract_foreground_to_new_layer"


def _candidate_extract_to_layer_label(mode: Sam3ExtractLayerMode) -> str:
    if mode == "background":
        return _ts("BG")
    return _ts("FG")


def _candidate_action_id(prefix: str, candidate_id: str) -> str:
    return f"{prefix}{quote(str(candidate_id), safe='')}"


def _candidate_id_from_action_id(prefix: str, action_id: str) -> str | None:
    if not action_id.startswith(prefix):
        return None
    encoded = action_id[len(prefix) :]
    if not encoded:
        return None
    candidate_id = unquote(encoded).strip()
    return candidate_id or None


def _extract_mode_from_action_id(action_id: str) -> Sam3ExtractLayerMode | None:
    if action_id == _CONTEXT_ACTION_EXTRACT_CURRENT_FOREGROUND or action_id.startswith(
        _CONTEXT_CANDIDATE_EXTRACT_FOREGROUND_ACTION_PREFIX
    ):
        return "foreground"
    if action_id == _CONTEXT_ACTION_EXTRACT_CURRENT_BACKGROUND or action_id.startswith(
        _CONTEXT_CANDIDATE_EXTRACT_BACKGROUND_ACTION_PREFIX
    ):
        return "background"
    return None


def _extract_layer_name(mode: Sam3ExtractLayerMode) -> str:
    if mode == "background":
        return "SAM3 Background"
    return "SAM3 Foreground"


def _combine_candidate_ids(value: Iterable[str] | None) -> tuple[str, ...]:
    if value is None or isinstance(value, (str, bytes, bytearray, Mapping)):
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        candidate_id = str(item).strip()
        if not candidate_id or candidate_id in seen:
            continue
        result.append(candidate_id)
        seen.add(candidate_id)
    return tuple(result)


def _combined_mask_candidate(
    session: Sam3SelectionSession,
    *,
    candidate_id: str,
    mask_asset_id: str,
    mask_asset_bbox: tuple[int, int, int, int],
    mask_asset_width: int,
    mask_asset_height: int,
    mode: MaskCombineMode,
    source_candidate_ids: Sequence[str],
    source_mask_asset_ids: Sequence[str],
) -> MaskCandidate:
    width = max(0, int(mask_asset_width))
    height = max(0, int(mask_asset_height))
    return MaskCandidate(
        candidate_id=candidate_id,
        source_image_id=session.candidate_set.source_ref.snapshot_id,
        prompt=session.candidate_set.prompt,
        label=f"combined_{mode}",
        score=None,
        bbox=tuple(int(item) for item in mask_asset_bbox),
        area_px=width * height,
        mask_asset_id=mask_asset_id,
        preview_asset_id=None,
        instance_index=len(session.candidate_set.candidates),
        group_id="sam3-combined",
        provenance={
            "provider": "local",
            "source": "sam3_ai_select.combine_selected",
            "operation": "combine",
            "mode": mode,
            "sourceCandidateIds": list(source_candidate_ids),
            "baseMaskAssetIds": list(source_mask_asset_ids),
        },
    )


def _default_combine_asset_id() -> str:
    return f"sam3-combined-mask-{uuid4().hex}"


def _refined_mask_candidate(
    session: Sam3SelectionSession,
    *,
    base_candidate: MaskCandidate,
    candidate_id: str,
    mask_asset_id: str,
    mask_asset_bbox: tuple[int, int, int, int],
    mask_asset_width: int,
    mask_asset_height: int,
    options: MaskRefineOptions,
) -> MaskCandidate:
    width = max(0, int(mask_asset_width))
    height = max(0, int(mask_asset_height))
    return MaskCandidate(
        candidate_id=candidate_id,
        source_image_id=session.candidate_set.source_ref.snapshot_id,
        prompt=session.candidate_set.prompt,
        label=f"refined_{base_candidate.label}",
        score=None,
        bbox=tuple(int(item) for item in mask_asset_bbox),
        area_px=width * height,
        mask_asset_id=mask_asset_id,
        preview_asset_id=None,
        instance_index=len(session.candidate_set.candidates),
        group_id="sam3-refined",
        provenance={
            "provider": "local",
            "source": "sam3_ai_select.mask_refine_selected",
            "operation": "mask_refine",
            "baseCandidateId": base_candidate.candidate_id,
            "baseMaskAssetId": str(base_candidate.mask_asset_id),
            "options": options.to_dict(),
        },
    )


def _default_refined_asset_id() -> str:
    return f"sam3-refined-mask-{uuid4().hex}"


def _default_refined_candidate_id() -> str:
    return f"sam3-refined-cand-{uuid4().hex}"


def _manga_mask_assistant_protect_text_asset_id(package: Sam3ComicMaskPackage) -> str:
    package_id = _sam3_asset_id_token(package.package_id)
    source_page_id = _sam3_asset_id_token(package.source_page_id)
    return f"{package_id}_{source_page_id}_protect_text"


def _manga_mask_assistant_mask_asset_available(
    asset_store: Sam3AssetStore,
    asset_id: object,
) -> bool:
    clean_asset_id = str(asset_id or "").strip()
    if not clean_asset_id:
        return False
    try:
        asset_store.load_mask_asset(clean_asset_id)
    except (OSError, ValueError):
        return False
    return True


def _manga_mask_assistant_active_ai_effect_layer_id(canvas: object | None) -> str | None:
    if canvas is None:
        return None
    layer_id = _canvas_active_layer_id(canvas)
    if layer_id is None:
        return None
    layer = _canvas_layer_by_id(canvas, layer_id)
    if (
        layer is None
        or getattr(layer, "layer_type", None) != "ai_effect"
        or getattr(layer, "ai_effect", None) is None
    ):
        return None
    return layer_id


def _sam3_asset_id_token(value: object) -> str:
    text = str(value or "").strip()
    clean = "".join(char if char in _SAFE_SAM3_ASSET_ID_CHARS else "_" for char in text).strip(
        "._-"
    )
    return clean or "unknown"


def _default_combine_candidate_id() -> str:
    return f"sam3-combined-cand-{uuid4().hex}"


def _run_status_state_payload(
    status: str,
    *,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    clean_status = str(status).strip() or "idle"
    clean_codes = [code for code in (str(item).strip() for item in blocked_reason_codes) if code]
    clean_error = str(error_type).strip() if error_type is not None else None
    return {
        "status": clean_status,
        "blockedReasonCodes": clean_codes,
        "errorType": clean_error or None,
    }


def _run_status_text(
    readiness: Mapping[str, Any],
    run_status_state: Mapping[str, Any] | None,
) -> str:
    state = run_status_state if isinstance(run_status_state, Mapping) else {}
    status = str(state.get("status") or "idle").strip()
    if status == "running":
        text = _ts("Running")
    elif status == "completed":
        text = _ts("Completed")
    elif status == "blocked":
        code = _first_text(state.get("blockedReasonCodes"))
        text = _status_with_detail(_ts("Blocked"), code)
    elif status == "failed":
        error_type = _clean_optional_text(state.get("errorType"))
        text = _status_with_detail(_ts("Failed"), error_type)
    elif bool(readiness.get("ready", False)):
        text = _ts("Ready")
    else:
        code = _first_text(readiness.get("blockedReasonCodes"))
        text = _status_with_detail(_ts("Blocked"), code) if code else _ts("Idle")
    return text


def _status_with_detail(label: str, detail: str | None) -> str:
    if detail:
        return f"{label}: {detail}"
    return label


def _first_text(value: object) -> str | None:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        for item in value:
            text = _clean_optional_text(item)
            if text:
                return text
    return None


def _preview_output_image_path_candidate(record: object) -> str | None:
    images = getattr(record, "output_images", None)
    if not isinstance(images, Iterable) or isinstance(images, (str, bytes, bytearray, Mapping)):
        return None
    for image in images:
        image_payload = _mapping(image)
        if image_payload is None:
            continue
        for key in ("path", "relativePath", "image_path", "imagePath"):
            path = _clean_optional_text(image_payload.get(key))
            if path is not None:
                return path
    return None


def _safe_preview_output_image_path(value: str) -> tuple[str | None, str | None]:
    path_text = _clean_optional_text(value)
    if path_text is None:
        return (None, "preview_output_image_path_missing")
    normalized = path_text.replace("\\", "/")
    if (
        "://" in normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or (len(normalized) >= 2 and normalized[1] == ":")
        or normalized.endswith("/")
        or "//" in normalized
    ):
        return (None, "preview_output_image_path_unsafe")

    segments = normalized.split("/")
    if not segments or any(segment in {"", ".", ".."} for segment in segments):
        return (None, "preview_output_image_path_unsafe")
    return (PurePosixPath(*segments).as_posix(), None)


def _preview_output_file_exists(history_store: object, relative_path: str) -> bool:
    return _preview_output_existing_file_path(history_store, relative_path) is not None


def _preview_output_existing_file_path(
    history_store: object,
    relative_path: str,
) -> Path | None:
    base_path_value = getattr(history_store, "base_path", None)
    if base_path_value is None:
        return None
    try:
        base_path = Path(base_path_value)
        base_resolved = base_path.resolve(strict=False)
        full_path = (base_path / relative_path).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return None
    if full_path != base_resolved and base_resolved not in full_path.parents:
        return None
    return full_path if full_path.is_file() else None


def _preview_apply_image_array(image_file: Path) -> Any | None:
    try:
        import numpy as np  # noqa: PLC0415
        from PySide6.QtGui import QImage  # noqa: PLC0415

        image = QImage(str(image_file))
        if image.isNull():
            return None
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        ptr = image.bits()
        return np.frombuffer(ptr, dtype=np.uint8).reshape((image.height(), image.width(), 4)).copy()
    except Exception:
        logger.debug("SAM3 preview apply image decode failed", exc_info=True)
        return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
        return []
    items: list[str] = []
    for item in value:
        text = _clean_optional_text(item)
        if text is not None:
            items.append(text)
    return items


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _context_apply_blocked_reason_codes(
    apply_plan: Sam3SelectionApplyPlan | None,
    asset_store: Sam3AssetStore | None,
) -> list[str]:
    if apply_plan is None:
        return ["ai_select_session_missing"]
    if not apply_plan.ready:
        return [reason.code for reason in apply_plan.blocked_reasons]
    if asset_store is None:
        return ["ai_select_asset_store_missing"]
    return []


def _context_refine_blocked_reason_codes(session: Sam3SelectionSession | None) -> list[str]:
    if session is None:
        return ["ai_select_session_missing"]
    candidate = session.active_candidate
    if candidate is None:
        return ["candidate_selection_missing"]
    if not str(candidate.mask_asset_id or "").strip():
        return ["candidate_mask_missing"]
    return []


def _context_layer_mask_apply_blocked_reason_codes(
    session: Sam3SelectionSession | None,
    asset_store: Sam3AssetStore | None,
) -> list[str]:
    blocked = _context_refine_blocked_reason_codes(session)
    if blocked:
        return blocked
    if asset_store is None:
        return ["ai_select_asset_store_missing"]
    return []


def _context_save_mask_asset_blocked_reason_codes(
    session: Sam3SelectionSession | None,
    asset_store: Sam3AssetStore | None,
    history_store: object | None,
) -> list[str]:
    blocked = _context_refine_blocked_reason_codes(session)
    if blocked:
        return blocked
    if asset_store is None:
        return ["ai_select_asset_store_missing"]
    if history_store is None:
        return ["generation_history_store_missing"]
    if not callable(getattr(history_store, "add_record", None)):
        return ["generation_history_store_unavailable"]
    return []


def _context_combine_selected_blocked_reason_codes(
    session: Sam3SelectionSession | None,
    asset_store: Sam3AssetStore | None,
) -> list[str]:
    if session is None:
        return ["ai_select_session_missing"]
    if asset_store is None:
        return ["ai_select_asset_store_missing"]
    available_ids = set(session.available_candidate_ids)
    combinable_count = sum(
        1
        for candidate in session.candidate_set.candidates
        if candidate.candidate_id in available_ids and str(candidate.mask_asset_id or "").strip()
    )
    if combinable_count < 2:
        return ["candidate_multi_selection_missing"]
    return []


def _context_mask_refine_blocked_reason_codes(
    session: Sam3SelectionSession | None,
    asset_store: Sam3AssetStore | None,
) -> list[str]:
    if session is None:
        return ["ai_select_session_missing"]
    if asset_store is None:
        return ["ai_select_asset_store_missing"]
    candidate = session.active_candidate
    if candidate is None:
        return ["candidate_selection_missing"]
    if not str(candidate.mask_asset_id or "").strip():
        return ["candidate_mask_missing"]
    return []


def _context_candidate_group_to_layer_group_blocked_reason_codes(
    session: Sam3SelectionSession | None,
    asset_store: Sam3AssetStore | None,
) -> list[str]:
    if session is None:
        return ["ai_select_session_missing"]
    if asset_store is None:
        return ["ai_select_asset_store_missing"]
    available_ids = set(session.available_candidate_ids)
    available_candidates = [
        candidate
        for candidate in session.candidate_set.candidates
        if candidate.candidate_id in available_ids
    ]
    if len(available_candidates) < 2:
        return ["candidate_multi_selection_missing"]
    masked_count = sum(
        1 for candidate in available_candidates if str(candidate.mask_asset_id or "").strip()
    )
    if masked_count < 2:
        return ["candidate_mask_missing"]
    return []


def _context_action_payload(
    action_id: str,
    kind: str,
    label: str,
    *,
    enabled: bool = False,
    checked: bool = False,
    operation: str | None = None,
    candidate_id: str | None = None,
    selected: bool | None = None,
    visible: bool | None = None,
    rank: int | None = None,
    text: str | None = None,
    blocked_reason_codes: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": action_id,
        "kind": kind,
        "label": label,
        "enabled": bool(enabled),
        "checked": bool(checked),
        "blockedReasonCodes": list(blocked_reason_codes or []),
    }
    if operation is not None:
        payload["operation"] = operation
    if candidate_id is not None:
        payload["candidateId"] = candidate_id
    if selected is not None:
        payload["selected"] = bool(selected)
    if visible is not None:
        payload["visible"] = bool(visible)
    if rank is not None:
        payload["rank"] = int(rank)
    if text is not None:
        payload["text"] = text
    return payload


# Context toolbar row assignment: row 0 = run / candidate navigation /
# operation toggles, row 1 = apply family, row 2 = generation + export.
# The list order below is the API contract (pinned by tests); rows only
# affect the visual grouping.
_CONTEXT_TOOLBAR_ROW_RUN = 0
_CONTEXT_TOOLBAR_ROW_APPLY = 1
_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT = 2


def _context_toolbar_widgets() -> list[ContextWidgetSpec]:
    return [
        ContextWidgetSpec(
            key=_RUN_ACTION_ID,
            kind="button",
            label=_ts("Run"),
            row=_CONTEXT_TOOLBAR_ROW_RUN,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_RUN_STATUS,
            kind="label",
            label=_ts("Idle"),
            row=_CONTEXT_TOOLBAR_ROW_RUN,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_STATUS,
            kind="label",
            label="0/0",
            row=_CONTEXT_TOOLBAR_ROW_RUN,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_SUMMARY,
            kind="label",
            label=_ts("No candidate"),
            row=_CONTEXT_TOOLBAR_ROW_RUN,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_PREVIOUS,
            kind="button",
            label=_ts("Previous"),
            row=_CONTEXT_TOOLBAR_ROW_RUN,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_NEXT,
            kind="button",
            label=_ts("Next"),
            row=_CONTEXT_TOOLBAR_ROW_RUN,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_REJECT,
            kind="button",
            label=_ts("Reject"),
            row=_CONTEXT_TOOLBAR_ROW_RUN,
        ),
        ContextWidgetSpec(
            key=_REFINE_ACTION_ID,
            kind="button",
            label=_ts("AI Refine..."),
            row=_CONTEXT_TOOLBAR_ROW_RUN,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_APPLY_CURRENT,
            kind="button",
            label=_ts("Apply"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_APPLY_CURRENT_AS_LAYER_MASK,
            kind="button",
            label=_ts("Apply as Layer Mask"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_USE_CURRENT_AS_INPAINT_MASK,
            kind="button",
            label=_ts("Use as Inpaint Mask"),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_PREVIEW_CURRENT_INPAINT,
            kind="button",
            label=_ts("Preview"),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_USE_SELECTION_AS_INPAINT_MASK,
            kind="button",
            label=_ts("Use Selection as Inpaint Mask"),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT,
            kind="button",
            label=_ts("Preview Selection Inpaint"),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_APPLY_CURRENT_AS_CLIPPING_MASK,
            kind="button",
            label=_ts("Apply as Clipping Mask"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_APPLY_CURRENT_TO_NEW_LAYER,
            kind="button",
            label=_ts("Cut to New Layer"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_EXTRACT_CURRENT_FOREGROUND,
            kind="button",
            label=_ts("Extract Foreground"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_EXTRACT_CURRENT_BACKGROUND,
            kind="button",
            label=_ts("Extract Background"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_SAVE_CURRENT_MASK_ASSET,
            kind="button",
            label=_ts("Save Mask Asset"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_COMBINE_SELECTED,
            kind="button",
            label=_ts("Combine Selected"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_GROUP_SELECTED_TO_LAYER_GROUP,
            kind="button",
            label=_ts("Layer Group"),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_MASK_REFINE_SELECTED,
            kind="button",
            label=_ts("Refine Mask..."),
            row=_CONTEXT_TOOLBAR_ROW_APPLY,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_EXPORT_CURRENT_CANDIDATE,
            kind="button",
            label=_ts("Export PNG..."),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_EXPORT_ALL_CANDIDATES,
            kind="button",
            label=_ts("Export All..."),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_EXPORT_SPRITE_SHEET,
            kind="button",
            label=_ts("Export Sheet..."),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_EXPORT_SELECTION,
            kind="button",
            label=_ts("Export Selection..."),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        ContextWidgetSpec(
            key=_CONTEXT_ACTION_USE_CURRENT_AS_BACKGROUND_GENERATION_MASK,
            kind="button",
            label=_ts("Protect Subject (BG Mask)"),
            row=_CONTEXT_TOOLBAR_ROW_GENERATION_EXPORT,
        ),
        *[
            ContextWidgetSpec(
                key=f"{_CONTEXT_OPERATION_ACTION_PREFIX}{operation}",
                kind="button",
                label=_operation_label(operation),
                row=_CONTEXT_TOOLBAR_ROW_RUN,
            )
            for operation in SAM3_AI_SELECT_OPERATION_VALUES
        ],
        # The one-click apply-with-operation actions (sam3_ai_select.apply.add /
        # .subtract / .intersect) stay dispatchable and in the context action
        # snapshot, but are not toolbar buttons: they duplicate the operation
        # toggles + Apply and bloated the toolbar.
    ]


def _context_action_by_id(
    snapshot: Mapping[str, Any],
    action_id: str,
) -> dict[str, Any] | None:
    actions = snapshot.get("actions", [])
    if not isinstance(actions, list):
        return None
    for action in actions:
        if isinstance(action, Mapping) and action.get("id") == action_id:
            return dict(action)
    return None


def _is_refine_context_action_id(action_id: str) -> bool:
    return action_id == _REFINE_ACTION_ID or action_id.startswith(
        _CONTEXT_CANDIDATE_REFINE_ACTION_PREFIX
    )


def _is_preview_submit_context_action_id(action_id: str) -> bool:
    return action_id in {
        _CONTEXT_ACTION_PREVIEW_CURRENT_INPAINT,
        _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT,
    } or action_id.startswith(_CONTEXT_CANDIDATE_PREVIEW_INPAINT_ACTION_PREFIX)


def _is_manga_inpaint_submit_bound_action_id(action_id: str) -> bool:
    target = parse_manga_mask_assistant_action_id(action_id)
    return target is not None and target.action == _MANGA_MASK_ASSISTANT_SEND_TO_INPAINT_ACTION


def _async_context_blocked_reason_codes(action_id: str) -> list[str]:
    if action_id == _RUN_ACTION_ID:
        return ["ai_select_run_requires_async_executor"]
    if _is_refine_context_action_id(action_id):
        return ["ai_select_refine_requires_async_executor"]
    return []


def _disabled_apply_action_result_payload(  # noqa: PLR0911
    action_id: str,
    blocked_reason_codes: Iterable[str],
) -> dict[str, Any] | None:
    if action_id == _CONTEXT_ACTION_COMBINE_SELECTED:
        return _combine_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id == _CONTEXT_ACTION_MASK_REFINE_SELECTED:
        return _mask_refine_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id == _CONTEXT_ACTION_GROUP_SELECTED_TO_LAYER_GROUP:
        return _candidate_group_to_layer_group_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id in {
        _CONTEXT_ACTION_APPLY_CURRENT_AS_LAYER_MASK,
        _CONTEXT_ACTION_ADD_CURRENT_TO_LAYER_MASK,
    } or action_id.startswith(_CONTEXT_CANDIDATE_APPLY_LAYER_MASK_ACTION_PREFIX):
        return _layer_mask_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id in {
        _CONTEXT_ACTION_USE_CURRENT_AS_INPAINT_MASK,
        _CONTEXT_ACTION_USE_CURRENT_AS_BACKGROUND_GENERATION_MASK,
        _CONTEXT_ACTION_USE_SELECTION_AS_INPAINT_MASK,
    } or action_id.startswith(
        (
            _CONTEXT_CANDIDATE_USE_INPAINT_MASK_ACTION_PREFIX,
            _CONTEXT_CANDIDATE_USE_BACKGROUND_GENERATION_MASK_ACTION_PREFIX,
        )
    ):
        return _ai_effect_mask_slot_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id in {
        _CONTEXT_ACTION_PREVIEW_CURRENT_INPAINT,
        _CONTEXT_ACTION_PREVIEW_SELECTION_INPAINT,
    } or action_id.startswith(_CONTEXT_CANDIDATE_PREVIEW_INPAINT_ACTION_PREFIX):
        return _ai_effect_preview_request_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id == _CONTEXT_ACTION_APPLY_CURRENT_AS_CLIPPING_MASK or action_id.startswith(
        _CONTEXT_CANDIDATE_APPLY_CLIPPING_MASK_ACTION_PREFIX
    ):
        return _clipping_mask_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id == _CONTEXT_ACTION_APPLY_CURRENT_TO_NEW_LAYER or action_id.startswith(
        _CONTEXT_CANDIDATE_CUT_TO_LAYER_ACTION_PREFIX
    ):
        return _cut_to_layer_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id in _CONTEXT_ACTION_EXTRACT_TO_LAYER_IDS or action_id.startswith(
        (
            _CONTEXT_CANDIDATE_EXTRACT_FOREGROUND_ACTION_PREFIX,
            _CONTEXT_CANDIDATE_EXTRACT_BACKGROUND_ACTION_PREFIX,
        )
    ):
        return _extract_to_layer_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    if action_id == _CONTEXT_ACTION_SAVE_CURRENT_MASK_ASSET or action_id.startswith(
        _CONTEXT_CANDIDATE_SAVE_MASK_ASSET_ACTION_PREFIX
    ):
        return _save_mask_asset_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=blocked_reason_codes,
        )
    return None


def _context_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    session: Sam3SelectionSession | None = None,
    apply_result: Sam3SelectionApplyExecutionResult | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "schemaVersion": _CONTEXT_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "session": _session_state_payload(session) if session is not None else None,
        "apply": (_apply_event_payload(apply_result) if apply_result is not None else None),
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _combine_payload(
    *,
    combined: bool,
    candidate_set_id: str | None,
    candidate_id: str | None,
    mask_asset_id: str | None,
    mode: str | None,
    source_candidate_ids: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "combined": bool(combined),
        "candidateSetId": candidate_set_id,
        "candidateId": candidate_id,
        "maskAssetId": mask_asset_id,
        "mode": mode,
        "sourceCandidateIds": list(source_candidate_ids),
    }


def _combine_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    combined: bool = False,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    mask_asset_id: str | None = None,
    mode: str | None = None,
    source_candidate_ids: Sequence[str] = (),
    session: Sam3SelectionSession | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    combine_payload = None
    if combined or candidate_set_id is not None or candidate_id is not None:
        combine_payload = _combine_payload(
            combined=combined,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            mask_asset_id=mask_asset_id,
            mode=mode,
            source_candidate_ids=source_candidate_ids,
        )
    return {
        "schemaVersion": _COMBINE_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "combine": combine_payload,
        "session": _session_state_payload(session) if session is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _mask_refine_payload(
    *,
    refined: bool,
    candidate_set_id: str | None,
    base_candidate_id: str | None,
    base_mask_asset_id: str | None,
    refined_candidate_id: str | None,
    refined_mask_asset_id: str | None,
    options: Mapping[str, object] | None,
) -> dict[str, Any]:
    return {
        "refined": bool(refined),
        "candidateSetId": candidate_set_id,
        "baseCandidateId": base_candidate_id,
        "baseMaskAssetId": base_mask_asset_id,
        "refinedCandidateId": refined_candidate_id,
        "refinedMaskAssetId": refined_mask_asset_id,
        "options": dict(options) if options is not None else None,
    }


def _mask_refine_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    refined: bool = False,
    candidate_set_id: str | None = None,
    base_candidate_id: str | None = None,
    base_mask_asset_id: str | None = None,
    refined_candidate_id: str | None = None,
    refined_mask_asset_id: str | None = None,
    options: Mapping[str, object] | None = None,
    session: Sam3SelectionSession | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    refine_payload = None
    if refined or candidate_set_id is not None or base_candidate_id is not None:
        refine_payload = _mask_refine_payload(
            refined=refined,
            candidate_set_id=candidate_set_id,
            base_candidate_id=base_candidate_id,
            base_mask_asset_id=base_mask_asset_id,
            refined_candidate_id=refined_candidate_id,
            refined_mask_asset_id=refined_mask_asset_id,
            options=options,
        )
    return {
        "schemaVersion": _MASK_REFINE_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "maskRefine": refine_payload,
        "session": _session_state_payload(session) if session is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _candidate_group_to_layer_group_payload(
    *,
    created: bool,
    candidate_set_id: str | None,
    source_layer_id: str | None,
    source_candidate_ids: Sequence[str] = (),
    created_layer_ids: Sequence[str] = (),
    created_group_layer_id: str | None = None,
) -> dict[str, Any]:
    return {
        "created": bool(created),
        "candidateSetId": candidate_set_id,
        "sourceCandidateIds": list(source_candidate_ids),
        "sourceLayerId": source_layer_id,
        "createdLayerIds": list(created_layer_ids),
        "createdGroupLayerId": created_group_layer_id,
    }


def _candidate_group_to_layer_group_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    candidate_set_id: str | None = None,
    source_candidate_ids: Sequence[str] = (),
    source_layer_id: str | None = None,
    created_layer_ids: Sequence[str] = (),
    created_group_layer_id: str | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    created = status == "created"
    layer_group_payload = None
    if created:
        layer_group_payload = _candidate_group_to_layer_group_payload(
            created=created,
            candidate_set_id=candidate_set_id,
            source_candidate_ids=source_candidate_ids,
            source_layer_id=source_layer_id,
            created_layer_ids=created_layer_ids,
            created_group_layer_id=created_group_layer_id,
        )
    return {
        "schemaVersion": _CANDIDATE_GROUP_TO_LAYER_GROUP_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "layerGroup": layer_group_payload,
        "session": None,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _layer_mask_apply_payload(
    *,
    applied: bool,
    submitted: bool,
    candidate_set_id: str | None,
    candidate_id: str | None,
    target_layer_id: str | None,
    mask_enabled: bool = True,
) -> dict[str, Any]:
    return {
        "applied": bool(applied),
        "submitted": bool(submitted),
        "candidateSetId": candidate_set_id,
        "candidateId": candidate_id,
        "targetLayerId": target_layer_id,
        "maskEnabled": bool(mask_enabled),
    }


def _layer_mask_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    submitted: bool = False,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    target_layer_id: str | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    applied = status == "applied"
    layer_mask_payload = None
    if applied or submitted or candidate_set_id is not None or candidate_id is not None:
        layer_mask_payload = _layer_mask_apply_payload(
            applied=applied,
            submitted=submitted,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            target_layer_id=target_layer_id,
        )
    return {
        "schemaVersion": _LAYER_MASK_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "layerMask": layer_mask_payload,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _ai_effect_mask_slot_payload(
    *,
    bound: bool,
    submitted: bool,
    candidate_set_id: str | None,
    candidate_id: str | None,
    effect_layer_id: str | None,
    slot_id: str | None,
    mask_layer_id: str | None,
    exclude_blend: bool,
    generation_purpose: str | None = None,
) -> dict[str, Any]:
    payload = {
        "bound": bool(bound),
        "submitted": bool(submitted),
        "candidateSetId": candidate_set_id,
        "candidateId": candidate_id,
        "effectLayerId": effect_layer_id,
        "slotId": slot_id,
        "maskLayerId": mask_layer_id,
        "excludeBlend": bool(exclude_blend),
    }
    if generation_purpose is not None:
        # Only the background-generation action adds this key; the existing
        # inpaint bind payload shape stays byte-identical.
        payload["generationPurpose"] = generation_purpose
    return payload


def _ai_effect_mask_slot_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    submitted: bool = False,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    effect_layer_id: str | None = None,
    slot_id: str | None = None,
    mask_layer_id: str | None = None,
    exclude_blend: bool = True,
    generation_purpose: str | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    bound = status == "bound"
    mask_slot_payload = None
    if (
        bound
        or submitted
        or candidate_set_id is not None
        or candidate_id is not None
        or effect_layer_id is not None
        or mask_layer_id is not None
    ):
        mask_slot_payload = _ai_effect_mask_slot_payload(
            bound=bound,
            submitted=submitted,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            effect_layer_id=effect_layer_id,
            slot_id=slot_id,
            mask_layer_id=mask_layer_id,
            exclude_blend=exclude_blend,
            generation_purpose=generation_purpose,
        )
    return {
        "schemaVersion": _AI_EFFECT_MASK_SLOT_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "aiEffectMaskSlot": mask_slot_payload,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _ai_effect_preview_request_payload(
    *,
    prepared: bool,
    candidate_set_id: str | None,
    candidate_id: str | None,
    effect_layer_id: str | None,
    slot_id: str | None,
    request: Mapping[str, object] | None,
) -> dict[str, Any]:
    return {
        "prepared": bool(prepared),
        "candidateSetId": candidate_set_id,
        "candidateId": candidate_id,
        "effectLayerId": effect_layer_id,
        "slotId": slot_id,
        "request": dict(request or {}),
    }


def _ai_effect_preview_request_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    effect_layer_id: str | None = None,
    slot_id: str | None = None,
    request: Mapping[str, object] | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    prepared = status == "prepared"
    preview_payload = None
    if prepared:
        preview_payload = _ai_effect_preview_request_payload(
            prepared=prepared,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            effect_layer_id=effect_layer_id,
            slot_id=slot_id,
            request=request,
        )
    return {
        "schemaVersion": _AI_EFFECT_PREVIEW_REQUEST_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "previewRequest": preview_payload,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _ai_effect_preview_submit_request_payload(
    preview_request: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if preview_request is None:
        return None
    return {
        "prepared": bool(preview_request.get("prepared", False)),
        "candidateSetId": _clean_optional_text(preview_request.get("candidateSetId")),
        "candidateId": _clean_optional_text(preview_request.get("candidateId")),
        "effectLayerId": _clean_optional_text(preview_request.get("effectLayerId")),
        "slotId": _clean_optional_text(preview_request.get("slotId")),
    }


def _ai_effect_preview_submit_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    preview_request: Mapping[str, Any] | None = None,
    preview_submit: Mapping[str, object] | None = None,
    preview_history: Mapping[str, object] | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _AI_EFFECT_PREVIEW_SUBMIT_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "previewRequest": _ai_effect_preview_submit_request_payload(preview_request),
        "previewSubmit": dict(preview_submit) if preview_submit is not None else None,
        "previewHistory": dict(preview_history) if preview_history is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _manga_mask_assistant_inpaint_preview_submit_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    preview_submit: Mapping[str, Any] | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _MANGA_INPAINT_PREVIEW_SUBMIT_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "previewSubmit": dict(preview_submit) if preview_submit is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _manga_mask_assistant_inpaint_preview_history_record_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    preview_history: Mapping[str, Any] | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _MANGA_INPAINT_PREVIEW_HISTORY_RECORD_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "previewHistory": dict(preview_history) if preview_history is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _manga_mask_assistant_inpaint_preview_submit_summary(
    submit_result: Mapping[str, object],
) -> dict[str, Any]:
    preview_submit = _mapping(submit_result.get("previewSubmit")) or {}
    manga_inpaint = _mapping(preview_submit.get("mangaInpaint")) or {}
    job = _mapping(preview_submit.get("job")) or {}
    result_asset_ids = _string_list(job.get("resultAssetIds"))
    assets = _preview_submit_asset_snapshots(job.get("assets"))
    return {
        "submitted": bool(preview_submit.get("submitted", False)),
        "candidateId": _clean_optional_text(preview_submit.get("candidateId")),
        "effectLayerId": _clean_optional_text(preview_submit.get("effectLayerId")),
        "protectedMaskAssetId": _clean_optional_text(manga_inpaint.get("protectedMaskAssetId")),
        "jobId": _clean_optional_text(job.get("jobId")),
        "jobStatus": _clean_optional_text(job.get("status")),
        "resultAssetCount": len(result_asset_ids) or len(assets),
        "blockedReasonCodes": _string_list(submit_result.get("blockedReasonCodes")),
    }


def _preview_apply_readiness_history_lookup_payload(
    *,
    found: bool,
    status: str,
    history_job_id: str | None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "found": bool(found),
        "status": status,
        "historyJobId": history_job_id,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _preview_apply_readiness_history_blocked_result_payload(
    action_id: str,
    blocked_reason_codes: Iterable[str],
    *,
    history_job_id: str | None,
    history_status: str = "skipped",
    error_type: str | None = None,
) -> dict[str, Any]:
    reason_codes = list(blocked_reason_codes)
    return _preview_apply_readiness_history_action_result_payload(
        action_id,
        status="blocked",
        handled=False,
        preview_apply_readiness=None,
        history_lookup=_preview_apply_readiness_history_lookup_payload(
            found=False,
            status=history_status,
            history_job_id=history_job_id,
            blocked_reason_codes=reason_codes,
        ),
        blocked_reason_codes=reason_codes,
        error_type=error_type,
    )


def _preview_apply_readiness_history_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    preview_apply_readiness: Mapping[str, Any] | None = None,
    history_lookup: Mapping[str, object] | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _PREVIEW_APPLY_READINESS_HISTORY_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "previewApplyReadiness": (
            dict(preview_apply_readiness) if preview_apply_readiness is not None else None
        ),
        "historyLookup": dict(history_lookup) if history_lookup is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _preview_output_image_path_promotion_payload(
    *,
    updated: bool,
    status: str,
    history_job_id: str | None,
    image_path_available: bool,
    candidate_path_available: bool,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "updated": bool(updated),
        "status": status,
        "historyJobId": history_job_id,
        "imagePathAvailable": bool(image_path_available),
        "candidatePathAvailable": bool(candidate_path_available),
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _preview_output_image_path_history_blocked_result_payload(
    action_id: str,
    blocked_reason_codes: Iterable[str],
    *,
    history_job_id: str | None,
    history_lookup_status: str = "skipped",
    candidate_path_available: bool = False,
    error_type: str | None = None,
) -> dict[str, Any]:
    reason_codes = list(blocked_reason_codes)
    return _preview_output_image_path_history_action_result_payload(
        action_id,
        status="blocked",
        handled=False,
        image_path_promotion=_preview_output_image_path_promotion_payload(
            updated=False,
            status="blocked",
            history_job_id=history_job_id,
            image_path_available=False,
            candidate_path_available=candidate_path_available,
            blocked_reason_codes=reason_codes,
        ),
        history_lookup=_preview_apply_readiness_history_lookup_payload(
            found=history_lookup_status == "found",
            status=history_lookup_status,
            history_job_id=history_job_id,
            blocked_reason_codes=reason_codes,
        ),
        blocked_reason_codes=reason_codes,
        error_type=error_type,
    )


def _preview_output_image_path_history_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    image_path_promotion: Mapping[str, object] | None = None,
    history_lookup: Mapping[str, object] | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _PREVIEW_OUTPUT_IMAGE_PATH_HISTORY_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "imagePathPromotion": (
            dict(image_path_promotion) if image_path_promotion is not None else None
        ),
        "historyLookup": dict(history_lookup) if history_lookup is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _preview_apply_payload(
    *,
    applied: bool,
    status: str,
    history_job_id: str | None,
    target_layer_id: str | None,
    image_path_available: bool,
    generation_diff_qa_ok: bool | None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "applied": bool(applied),
        "status": status,
        "historyJobId": history_job_id,
        "targetLayerId": target_layer_id,
        "imagePathAvailable": bool(image_path_available),
        "generationDiffQAOk": generation_diff_qa_ok,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _preview_apply_history_blocked_result_payload(
    action_id: str,
    blocked_reason_codes: Iterable[str],
    *,
    history_job_id: str | None,
    target_layer_id: str | None,
    history_lookup_status: str = "skipped",
    image_path_available: bool = False,
    generation_diff_qa_ok: bool | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    reason_codes = list(blocked_reason_codes)
    return _preview_apply_history_action_result_payload(
        action_id,
        status="blocked",
        handled=False,
        preview_apply=_preview_apply_payload(
            applied=False,
            status="blocked",
            history_job_id=history_job_id,
            target_layer_id=target_layer_id,
            image_path_available=image_path_available,
            generation_diff_qa_ok=generation_diff_qa_ok,
            blocked_reason_codes=reason_codes,
        ),
        history_lookup=_preview_apply_readiness_history_lookup_payload(
            found=history_lookup_status == "found",
            status=history_lookup_status,
            history_job_id=history_job_id,
            blocked_reason_codes=reason_codes,
        ),
        blocked_reason_codes=reason_codes,
        error_type=error_type,
    )


def _preview_apply_history_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    preview_apply: Mapping[str, object] | None = None,
    history_lookup: Mapping[str, object] | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _PREVIEW_APPLY_HISTORY_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "previewApply": dict(preview_apply) if preview_apply is not None else None,
        "historyLookup": dict(history_lookup) if history_lookup is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _generation_diff_qa_summary_payload(
    qa: Sam3GenerationResultDiffQA,
) -> dict[str, Any]:
    return {
        "ok": qa.ok,
        "warningCodes": [warning.code for warning in qa.warnings],
        "changedAreaRatio": qa.changed_area_ratio,
        "protectedRegionCoverage": qa.protected_region_coverage,
        "protectedOverlapAreaRatio": qa.protected_overlap_area_ratio,
        "recommendedActions": list(qa.recommended_actions),
    }


def _generation_diff_qa_step_payload(
    *,
    qa: Sam3GenerationResultDiffQA | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    if qa is None:
        return {
            "ran": False,
            "ok": None,
            "qaStatus": None,
            "changedAreaRatio": None,
            "protectedRegionCoverage": None,
            "protectedOverlapAreaRatio": None,
            "warningCodes": [],
            "recommendedActions": [],
            "blockedReasonCodes": list(blocked_reason_codes),
        }
    return {
        "ran": True,
        "qaStatus": "passed" if qa.ok else "warning",
        **_generation_diff_qa_summary_payload(qa),
        "blockedReasonCodes": [],
    }


def _encode_qimage_to_png_bytes(image: Any) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    detached = image.copy()
    if detached.format() != QImage.Format.Format_RGBA8888:
        detached = detached.convertToFormat(QImage.Format.Format_RGBA8888)
    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("SAM3 diff QA PNG buffer could not be opened.")
    try:
        if not detached.save(buffer, "PNG"):
            raise RuntimeError("SAM3 diff QA PNG encode failed.")
    finally:
        buffer.close()
    return bytes(byte_array.data())


def _generation_diff_qa_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    generation_diff_qa: Sam3GenerationResultDiffQA | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _GENERATION_DIFF_QA_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "generationDiffQA": (
            _generation_diff_qa_summary_payload(generation_diff_qa)
            if generation_diff_qa is not None
            else None
        ),
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _generation_diff_qa_history_update_payload(
    *,
    updated: bool,
    status: str,
    history_job_id: str | None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "updated": bool(updated),
        "status": status,
        "historyJobId": history_job_id,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _generation_diff_qa_history_blocked_result_payload(
    action_id: str,
    blocked_reason_codes: Iterable[str],
    *,
    history_job_id: str | None,
    generation_diff_qa: Mapping[str, Any] | None = None,
    history_status: str = "skipped",
    error_type: str | None = None,
) -> dict[str, Any]:
    reason_codes = list(blocked_reason_codes)
    return _generation_diff_qa_history_action_result_payload(
        action_id,
        status="blocked",
        handled=False,
        generation_diff_qa=generation_diff_qa,
        history_update=_generation_diff_qa_history_update_payload(
            updated=False,
            status=history_status,
            history_job_id=history_job_id,
            blocked_reason_codes=reason_codes,
        ),
        blocked_reason_codes=reason_codes,
        error_type=error_type,
    )


def _generation_diff_qa_history_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    generation_diff_qa: Mapping[str, Any] | None = None,
    history_update: Mapping[str, object] | None = None,
    blocked_reason_codes: Iterable[str] = (),
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _GENERATION_DIFF_QA_HISTORY_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "generationDiffQA": dict(generation_diff_qa) if generation_diff_qa is not None else None,
        "historyUpdate": dict(history_update) if history_update is not None else None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _ai_effect_preview_submit_history_payload(
    *,
    recorded: bool,
    status: str,
    history_job_id: str | None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "recorded": bool(recorded),
        "status": status,
        "historyJobId": history_job_id,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _clipping_mask_apply_payload(
    *,
    applied: bool,
    submitted: bool,
    candidate_set_id: str | None,
    candidate_id: str | None,
    target_layer_id: str | None,
    mask_enabled: bool = True,
    clipping_enabled: bool = True,
) -> dict[str, Any]:
    return {
        "applied": bool(applied),
        "submitted": bool(submitted),
        "candidateSetId": candidate_set_id,
        "candidateId": candidate_id,
        "targetLayerId": target_layer_id,
        "maskEnabled": bool(mask_enabled),
        "clippingEnabled": bool(clipping_enabled),
    }


def _clipping_mask_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    submitted: bool = False,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    target_layer_id: str | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    applied = status == "applied"
    clipping_mask_payload = None
    if applied or submitted or candidate_set_id is not None or candidate_id is not None:
        clipping_mask_payload = _clipping_mask_apply_payload(
            applied=applied,
            submitted=submitted,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            target_layer_id=target_layer_id,
        )
    return {
        "schemaVersion": _CLIPPING_MASK_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "clippingMask": clipping_mask_payload,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _cut_to_layer_payload(
    *,
    applied: bool,
    submitted: bool,
    candidate_set_id: str | None,
    candidate_id: str | None,
    source_layer_id: str | None,
    created_layer_id: str | None,
) -> dict[str, Any]:
    return {
        "applied": bool(applied),
        "submitted": bool(submitted),
        "candidateSetId": candidate_set_id,
        "candidateId": candidate_id,
        "sourceLayerId": source_layer_id,
        "createdLayerId": created_layer_id,
    }


def _asset_export_scope_for_action_id(action_id: str) -> str:
    scope = _ASSET_EXPORT_SCOPE_BY_ACTION_ID.get(action_id)
    if scope is None:
        raise ValueError(f"Unknown SAM3 asset export action id: {action_id!r}")
    return scope


def _asset_export_event_payload(
    *,
    scope: str,
    status: str,
    file_count: int,
    skipped_count: int,
    blocked_reason_codes: Iterable[str],
) -> dict[str, Any]:
    # Counts only: absolute paths, file names, labels, and prompt text must
    # never reach events, results, or status lines.
    return {
        "tool": TOOL_ID,
        "scope": scope,
        "status": status,
        "fileCount": int(file_count),
        "skippedCount": int(skipped_count),
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _asset_export_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    scope: str,
    file_count: int = 0,
    skipped_count: int = 0,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "schemaVersion": _ASSET_EXPORT_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "scope": scope,
        "fileCount": int(file_count),
        "skippedCount": int(skipped_count),
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _enumerate_exportable_candidates(
    session: Sam3SelectionSession,
) -> tuple[list[tuple[int, str]], int]:
    """(index, mask_asset_id) list + maskless-skip count.

    Rejected candidates leave the set entirely (no index). Maskless
    candidates keep their slot (index gap) so file numbers stay aligned with
    the candidate list order.
    """

    available_ids = set(session.available_candidate_ids)
    exportable: list[tuple[int, str]] = []
    skipped = 0
    for candidate in session.candidate_set.candidates:
        if candidate.candidate_id not in available_ids:
            continue
        candidate_mask_id = str(candidate.mask_asset_id or "").strip()
        if not candidate_mask_id:
            skipped += 1
            continue
        exportable.append((len(exportable) + skipped + 1, candidate_mask_id))
    return exportable, skipped


def _sheet_image_suffix(image_format: str) -> str:
    return ".webp" if str(image_format).lower() == "webp" else ".png"


def _normalized_sheet_image_path(raw_path: str, *, image_format: str) -> Path:
    """Force the image suffix so the derived .json can never collide with the
    sheet itself (e.g. a user typing "sheet.json" for a PNG export must not
    have the atlas overwrite the image)."""

    suffix = _sheet_image_suffix(image_format)
    path = Path(raw_path)
    if not path.name.lower().endswith(suffix):
        path = path.with_name(f"{path.name}{suffix}")
    return path


def _sheet_atlas_path(image_path: Path, *, image_format: str) -> Path:
    suffix = _sheet_image_suffix(image_format)
    return image_path.with_name(f"{image_path.name[: -len(suffix)]}.json")


def _prompt_sprite_sheet_save_path(image_format: str) -> str:
    """Modal save dialog for the sprite sheet; module-level for tests."""

    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    if str(image_format).lower() == "webp":
        default_name = "sheet.webp"
        file_filter = "WebP Image (*.webp)"
    else:
        default_name = "sheet.png"
        file_filter = "PNG Image (*.png)"
    path, _selected_filter = QFileDialog.getSaveFileName(
        None,
        _ts("Export Sprite Sheet"),
        default_name,
        file_filter,
    )
    return str(path or "")


def _captured_export_layer(source_layer: Any) -> Any:
    """Immutable snapshot of the layer pixels/offset taken before the dialog.

    The composite pseudo layer is already an owned, immutable copy (offset 0),
    so it is passed through without a second copy.
    """

    from types import SimpleNamespace  # noqa: PLC0415

    if getattr(source_layer, "_sam3_export_owned", False):
        return source_layer
    return SimpleNamespace(
        image=source_layer.image.copy(),
        offset_x=int(getattr(source_layer, "offset_x", 0)),
        offset_y=int(getattr(source_layer, "offset_y", 0)),
    )


def _prompt_asset_export_save_path(default_name: str) -> str:
    """Modal save dialog; module-level for test monkeypatching."""

    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    path, _selected_filter = QFileDialog.getSaveFileName(
        None,
        _ts("Export PNG"),
        default_name,
        "PNG Image (*.png)",
    )
    return str(path or "")


def _prompt_asset_export_directory() -> str:
    """Modal directory dialog; module-level for test monkeypatching."""

    from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

    return str(QFileDialog.getExistingDirectory(None, _ts("Export All Candidates")) or "")


def _cut_to_layer_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    submitted: bool = False,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    source_layer_id: str | None = None,
    created_layer_id: str | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    applied = status == "applied"
    cut_to_layer_payload = None
    if (
        applied
        or submitted
        or candidate_set_id is not None
        or candidate_id is not None
        or source_layer_id is not None
        or created_layer_id is not None
    ):
        cut_to_layer_payload = _cut_to_layer_payload(
            applied=applied,
            submitted=submitted,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            source_layer_id=source_layer_id,
            created_layer_id=created_layer_id,
        )
    return {
        "schemaVersion": _CUT_TO_LAYER_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "cutToLayer": cut_to_layer_payload,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _extract_to_layer_payload(
    *,
    mode: Sam3ExtractLayerMode,
    applied: bool,
    submitted: bool,
    candidate_set_id: str | None,
    candidate_id: str | None,
    source_layer_id: str | None,
    created_layer_id: str | None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "applied": bool(applied),
        "submitted": bool(submitted),
        "candidateSetId": candidate_set_id,
        "candidateId": candidate_id,
        "sourceLayerId": source_layer_id,
        "createdLayerId": created_layer_id,
    }


def _extract_to_layer_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    mode: Sam3ExtractLayerMode | None = None,
    submitted: bool = False,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    source_layer_id: str | None = None,
    created_layer_id: str | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    applied = status == "applied"
    extract_mode = mode if mode is not None else _extract_mode_from_action_id(action_id)
    extract_to_layer_payload = None
    if extract_mode is not None and (
        applied
        or submitted
        or candidate_set_id is not None
        or candidate_id is not None
        or source_layer_id is not None
        or created_layer_id is not None
    ):
        extract_to_layer_payload = _extract_to_layer_payload(
            mode=extract_mode,
            applied=applied,
            submitted=submitted,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            source_layer_id=source_layer_id,
            created_layer_id=created_layer_id,
        )
    return {
        "schemaVersion": _EXTRACT_TO_LAYER_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "extractToLayer": extract_to_layer_payload,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _save_mask_asset_payload(
    *,
    saved: bool,
    candidate_set_id: str | None,
    candidate_id: str | None,
    mask_asset_id: str | None,
    history_job_id: str | None,
) -> dict[str, Any]:
    return {
        "saved": bool(saved),
        "candidateSetId": candidate_set_id,
        "candidateId": candidate_id,
        "maskAssetId": mask_asset_id,
        "historyJobId": history_job_id,
    }


def _save_mask_asset_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    saved: bool = False,
    candidate_set_id: str | None = None,
    candidate_id: str | None = None,
    mask_asset_id: str | None = None,
    history_job_id: str | None = None,
    blocked_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    save_payload = None
    if (
        saved
        or candidate_set_id is not None
        or candidate_id is not None
        or mask_asset_id is not None
    ):
        save_payload = _save_mask_asset_payload(
            saved=saved,
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            mask_asset_id=mask_asset_id,
            history_job_id=history_job_id,
        )
    return {
        "schemaVersion": _SAVE_MASK_ASSET_ACTION_RESULT_SCHEMA_VERSION,
        "tool": TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "saveMaskAsset": save_payload,
        "blockedReasonCodes": list(blocked_reason_codes),
    }


def _build_save_mask_asset_generation_record(
    *,
    settings: Sam3AiSelectToolSettings,
    session: Sam3SelectionSession,
    candidate: Any,
    mask_asset: Any,
) -> GenerationRecord:
    timestamp = datetime.now().astimezone().isoformat()
    prompt = _clean_optional_text(settings.prompt_text)
    snapshot = {
        "sam3_ai_select": {
            "version": _SAVE_MASK_ASSET_HISTORY_PAYLOAD_VERSION,
            "operation": "save_mask_asset",
            "candidate_set_id": session.candidate_set.set_id,
            "candidate_id": candidate.candidate_id,
            "mask_asset_id": mask_asset.asset_id,
            "mask_asset_kind": mask_asset.kind,
            "mask_asset_bbox": [int(item) for item in mask_asset.bbox],
            "mask_asset_content_hash": mask_asset.content_hash,
            "source_candidate_id": mask_asset.source_candidate_id,
            "selection_operation": session.operation,
            "source": "sam3.ai_select.save_mask_asset",
        }
    }
    return GenerationRecord(
        job_id=f"sam3-save-{uuid4().hex}",
        service_id=_SAVE_MASK_ASSET_HISTORY_SERVICE_ID,
        service_name="SAM3 AI Select",
        status="completed",
        created_at=timestamp,
        completed_at=timestamp,
        duration_sec=0.0,
        prompt=prompt,
        width=int(mask_asset.width),
        height=int(mask_asset.height),
        service_snapshot=snapshot,
        output_images=[],
    )


def _generation_record_with_generation_diff_qa(
    record: GenerationRecord,
    qa_summary: Mapping[str, Any],
    *,
    qa_status: str,
) -> GenerationRecord:
    snapshot = dict(record.service_snapshot or {})
    sam3_snapshot = dict(_mapping(snapshot.get("sam3_ai_select")) or {})
    sam3_snapshot["generation_diff_qa"] = _generation_diff_qa_history_snapshot(
        qa_summary,
        qa_status=qa_status,
    )
    snapshot["sam3_ai_select"] = sam3_snapshot
    return replace(record, service_snapshot=snapshot)


def _generation_diff_qa_history_snapshot(
    qa_summary: Mapping[str, Any],
    *,
    qa_status: str,
) -> dict[str, Any]:
    return {
        "version": _GENERATION_DIFF_QA_HISTORY_PAYLOAD_VERSION,
        "source": _GENERATION_DIFF_QA_HISTORY_SOURCE,
        "ok": bool(qa_summary.get("ok", False)),
        "status": "passed" if qa_status == "passed" else "warning",
        "warning_codes": _string_list(qa_summary.get("warningCodes")),
        "changed_area_ratio": _safe_float(qa_summary.get("changedAreaRatio")),
        "protected_region_coverage": _safe_optional_float(
            qa_summary.get("protectedRegionCoverage")
        ),
        "protected_overlap_area_ratio": _safe_optional_float(
            qa_summary.get("protectedOverlapAreaRatio")
        ),
        "recommended_actions": _string_list(qa_summary.get("recommendedActions")),
    }


def _build_ai_effect_preview_submit_generation_record(
    *,
    settings: Sam3AiSelectToolSettings,
    preview_submit: Mapping[str, Any],
) -> GenerationRecord:
    timestamp = datetime.now().astimezone().isoformat()
    prompt = _clean_optional_text(settings.prompt_text)
    job = _mapping(preview_submit.get("job")) or {}
    request_summary = _preview_submit_request_summary_snapshot(
        _mapping(preview_submit.get("requestSummary")) or {}
    )
    job_snapshot = _preview_submit_job_snapshot(job)
    output_images = _preview_submit_output_images(job)
    width, height = _preview_submit_first_output_size(output_images)
    job_id = _clean_optional_text(job_snapshot.get("jobId")) or f"sam3-preview-{uuid4().hex}"
    snapshot = {
        "sam3_ai_select": {
            "version": _AI_EFFECT_PREVIEW_SUBMIT_HISTORY_PAYLOAD_VERSION,
            "operation": "ai_effect_preview_submit",
            "candidate_set_id": _clean_optional_text(preview_submit.get("candidateSetId")),
            "candidate_id": _clean_optional_text(preview_submit.get("candidateId")),
            "effect_layer_id": _clean_optional_text(preview_submit.get("effectLayerId")),
            "slot_id": _clean_optional_text(preview_submit.get("slotId")),
            "request_summary": request_summary,
            "job": job_snapshot,
            "source": _AI_EFFECT_PREVIEW_SUBMIT_HISTORY_SOURCE,
        }
    }
    return GenerationRecord(
        job_id=job_id,
        service_id=_SAVE_MASK_ASSET_HISTORY_SERVICE_ID,
        service_name="SAM3 AI Select",
        status=_preview_submit_history_status(job_snapshot.get("status")),
        created_at=timestamp,
        completed_at=timestamp,
        duration_sec=0.0,
        prompt=prompt,
        width=width,
        height=height,
        service_snapshot=snapshot,
        output_images=output_images,
    )


def _build_manga_inpaint_preview_submit_generation_record(
    *,
    settings: Sam3AiSelectToolSettings,
    metadata: Mapping[str, object],
) -> GenerationRecord:
    timestamp = datetime.now().astimezone().isoformat()
    prompt = _clean_optional_text(settings.prompt_text)
    job = _mapping(metadata.get("job")) or {}
    request_summary = _preview_submit_request_summary_snapshot(
        _mapping(metadata.get("requestSummary")) or {}
    )
    job_snapshot = _preview_submit_job_snapshot(job)
    manga_inpaint = _manga_inpaint_history_snapshot(_mapping(metadata.get("mangaInpaint")) or {})
    output_images = _preview_submit_output_images(job)
    width, height = _preview_submit_first_output_size(output_images)
    job_id = _clean_optional_text(job_snapshot.get("jobId")) or f"sam3-manga-preview-{uuid4().hex}"
    snapshot = {
        "sam3_ai_select": {
            "version": _MANGA_INPAINT_PREVIEW_HISTORY_PAYLOAD_VERSION,
            "operation": "manga_inpaint_preview_submit",
            "source": _MANGA_INPAINT_PREVIEW_HISTORY_SOURCE,
            "metadata_schema_version": _clean_optional_text(metadata.get("schemaVersion")),
            "candidate_set_id": _clean_optional_text(metadata.get("candidateSetId")),
            "candidate_id": _clean_optional_text(metadata.get("candidateId")),
            "effect_layer_id": _clean_optional_text(metadata.get("effectLayerId")),
            "slot_id": _clean_optional_text(metadata.get("slotId")),
            "request_summary": request_summary,
            "job": job_snapshot,
            "manga_inpaint": manga_inpaint,
        }
    }
    return GenerationRecord(
        job_id=job_id,
        service_id=_SAVE_MASK_ASSET_HISTORY_SERVICE_ID,
        service_name="SAM3 AI Select",
        status=_preview_submit_history_status(job_snapshot.get("status")),
        created_at=timestamp,
        completed_at=timestamp,
        duration_sec=0.0,
        prompt=prompt,
        width=width,
        height=height,
        service_snapshot=snapshot,
        output_images=output_images,
    )


def _manga_inpaint_history_snapshot(manga_inpaint: Mapping[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in (
        "schemaVersion",
        "candidateId",
        "category",
        "inpaintMaskAssetId",
        "protectedMaskAssetId",
    ):
        text = _clean_optional_text(manga_inpaint.get(key))
        if text is not None:
            snapshot[key] = text
    if "requiresUserConfirmation" in manga_inpaint:
        snapshot["requiresUserConfirmation"] = bool(manga_inpaint.get("requiresUserConfirmation"))
    return snapshot


def _preview_submit_request_summary_snapshot(summary: Mapping[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in ("layerId", "metadataSource"):
        text = _clean_optional_text(summary.get(key))
        if text is not None:
            snapshot[key] = text
    for key in ("applyToCanvas", "persistOverrides", "candidateRequest"):
        if key in summary:
            snapshot[key] = bool(summary.get(key))
    candidate_count = _optional_positive_int(summary.get("candidateCount"))
    if candidate_count is not None:
        snapshot["candidateCount"] = candidate_count
    return snapshot


def _preview_submit_job_snapshot(job: Mapping[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in ("jobId", "status", "jobClass", "relatedLayerId"):
        text = _clean_optional_text(job.get(key))
        if text is not None:
            snapshot[key] = text
    progress = job.get("progress")
    if isinstance(progress, int | float) and not isinstance(progress, bool):
        snapshot["progress"] = progress
    result_asset_ids = _string_list(job.get("resultAssetIds"))
    if result_asset_ids:
        snapshot["resultAssetIds"] = result_asset_ids
    assets = _preview_submit_asset_snapshots(job.get("assets"))
    if assets:
        snapshot["assets"] = assets
    return snapshot


def _preview_submit_asset_snapshots(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
        return []
    assets: list[dict[str, Any]] = []
    for item in value:
        asset = _mapping(item)
        if asset is None:
            continue
        asset_snapshot: dict[str, Any] = {}
        for key in ("assetId", "kind", "mimeType"):
            text = _clean_optional_text(asset.get(key))
            if text is not None:
                asset_snapshot[key] = text
        dimensions = _preview_submit_dimensions_snapshot(asset.get("dimensions"))
        if dimensions:
            asset_snapshot["dimensions"] = dimensions
        if asset_snapshot:
            assets.append(asset_snapshot)
    return assets


def _preview_submit_output_images(job: Mapping[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for asset in _preview_submit_asset_snapshots(job.get("assets")):
        image: dict[str, Any] = {}
        asset_id = _clean_optional_text(asset.get("assetId"))
        if asset_id is not None:
            image["asset_id"] = asset_id
        kind = _clean_optional_text(asset.get("kind"))
        if kind is not None:
            image["kind"] = kind
        mime_type = _clean_optional_text(asset.get("mimeType"))
        if mime_type is not None:
            image["mime_type"] = mime_type
        dimensions = _mapping(asset.get("dimensions"))
        if dimensions is not None:
            width = _optional_positive_int(dimensions.get("width"))
            height = _optional_positive_int(dimensions.get("height"))
            if width is not None:
                image["width"] = width
            if height is not None:
                image["height"] = height
        if image:
            images.append(image)
    return images


def _preview_submit_dimensions_snapshot(value: object) -> dict[str, int]:
    dimensions = _mapping(value)
    if dimensions is None:
        return {}
    snapshot: dict[str, int] = {}
    for key in ("width", "height"):
        number = _optional_positive_int(dimensions.get(key))
        if number is not None:
            snapshot[key] = number
    return snapshot


def _preview_submit_first_output_size(
    output_images: Sequence[Mapping[str, Any]],
) -> tuple[int | None, int | None]:
    if not output_images:
        return (None, None)
    first = output_images[0]
    return (
        _optional_positive_int(first.get("width")),
        _optional_positive_int(first.get("height")),
    )


def _preview_submit_history_status(value: object) -> str:
    status = (_clean_optional_text(value) or "").casefold()
    if status in {"failed", "failure", "error"}:
        return "failed"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    return "completed"


def _safe_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _safe_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    else:
        return None
    return number if number > 0 else None


def _selection_mask_to_array(mask: object) -> object:
    """Convert an Alpha8 selection mask QImage to a stride-free uint8 array."""

    from loomstroke.core.selection_state import SelectionStateService  # noqa: PLC0415

    return SelectionStateService.selection_mask_to_numpy(mask)


def _canvas_active_layer_id(canvas: object) -> str | None:
    current_layer = getattr(canvas, "current_layer", None)
    if callable(current_layer):
        try:
            layer_ref = current_layer()
        except Exception:
            logger.debug("SAM3 layer mask current layer lookup failed", exc_info=True)
        else:
            layer_id = _clean_optional_text(getattr(layer_ref, "id", None))
            if layer_id is not None:
                return layer_id

    active_layer = getattr(canvas, "active_layer", None)
    return _clean_optional_text(getattr(active_layer, "id", None))


def _canvas_layer_by_id(canvas: object, layer_id: str) -> object | None:
    for method_name in ("_find_layer_by_id_tree", "_get_layer_by_id"):
        finder = getattr(canvas, method_name, None)
        if callable(finder):
            try:
                layer = finder(layer_id)
            except Exception:
                logger.debug("SAM3 canvas layer lookup failed for %s", layer_id, exc_info=True)
                layer = None
            if layer is not None:
                return layer

    roots = getattr(canvas, "_layers", None)
    if isinstance(roots, list):
        return _walk_canvas_layers(roots, layer_id)
    return None


def _walk_canvas_layers(layers: Sequence[object], layer_id: str) -> object | None:
    for layer in layers:
        if _clean_optional_text(getattr(layer, "id", None)) == layer_id:
            return layer
        children = getattr(layer, "children", None)
        if isinstance(children, list):
            found = _walk_canvas_layers(children, layer_id)
            if found is not None:
                return found
    return None


def _canvas_layer_mask(canvas: object, layer_id: str) -> object | None:
    get_layer_mask = getattr(canvas, "get_layer_mask", None)
    if callable(get_layer_mask):
        try:
            return get_layer_mask(layer_id)
        except Exception:
            logger.debug("SAM3 layer mask lookup failed for %s", layer_id, exc_info=True)

    current_layer = getattr(canvas, "current_layer", None)
    layer = None
    if callable(current_layer):
        try:
            layer = current_layer()
        except Exception:
            logger.debug("SAM3 current layer lookup failed for mask add", exc_info=True)
    if layer is None:
        layer = getattr(canvas, "active_layer", None)
    if _clean_optional_text(getattr(layer, "id", None)) != layer_id:
        return None
    mask = getattr(layer, "layer_mask", None)
    copy = getattr(mask, "copy", None)
    return copy() if callable(copy) else mask


def _canvas_size(canvas: object) -> tuple[int, int] | None:
    snapshot_getter = getattr(canvas, "get_canvas_state_snapshot", None)
    if callable(snapshot_getter):
        try:
            snapshot = snapshot_getter()
        except Exception:
            logger.debug("SAM3 layer mask canvas snapshot lookup failed", exc_info=True)
        else:
            size = _positive_size(
                getattr(snapshot, "width", None),
                getattr(snapshot, "height", None),
            )
            if size is not None:
                return size

    size_getter = getattr(canvas, "get_canvas_size", None)
    if callable(size_getter):
        try:
            raw_size = size_getter()
        except Exception:
            logger.debug("SAM3 layer mask canvas size lookup failed", exc_info=True)
        else:
            if isinstance(raw_size, (list, tuple)) and len(raw_size) >= 2:
                size = _positive_size(raw_size[0], raw_size[1])
                if size is not None:
                    return size

    return _positive_size(
        getattr(canvas, "_width", None),
        getattr(canvas, "_height", None),
    )


def _manga_mask_assistant_host_canvas(host_api: object) -> object | None:
    if host_api is None:
        return None

    canvas = getattr(host_api, "canvas", None)
    if canvas is not None:
        return canvas

    ui = getattr(host_api, "ui", None)
    get_main_window = getattr(ui, "get_main_window", None)
    if callable(get_main_window):
        try:
            main_window = get_main_window()
        except Exception:
            logger.debug("SAM3 Manga Mask Assistant main window lookup failed", exc_info=True)
        else:
            canvas = getattr(main_window, "canvas", None)
            if canvas is not None:
                return canvas

    main_window = getattr(ui, "_main_window", None)
    if main_window is None:
        return None
    return getattr(main_window, "canvas", None)


def _positive_size(width: object, height: object) -> tuple[int, int] | None:
    try:
        clean_width = int(width)  # type: ignore[arg-type]
        clean_height = int(height)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if clean_width <= 0 or clean_height <= 0:
        return None
    return (clean_width, clean_height)


def _candidate_list_action_scheduler(
    host: object,
) -> Callable[[str, dict[str, object] | None], bool] | None:
    manager = getattr(host, "_tool_manager", None)
    scheduler = getattr(manager, "schedule_tool_action_async", None)
    if not callable(scheduler):
        return None

    def _schedule(action_id: str, payload: dict[str, object] | None) -> bool:
        return bool(scheduler(TOOL_ID, action_id, payload))

    return _schedule


def _generation_history_store_from_host(host: object | None) -> object | None:
    if host is None:
        return None
    get_service = getattr(host, "get_service", None)
    if callable(get_service):
        try:
            service = get_service("generation_history")
        except Exception:
            logger.debug("SAM3 generation history service lookup failed", exc_info=True)
        else:
            if service is not None:
                return service
    return getattr(host, "generation_history", None)


def _operation_label(operation: str) -> str:
    labels = {
        "replace": _ts("Replace"),
        "add": _ts("Add"),
        "subtract": _ts("Subtract"),
        "intersect": _ts("Intersect"),
    }
    return labels.get(operation, operation)


def _apply_operation_action_label(operation: str) -> str:
    labels = {
        "add": _ts("Apply as Add"),
        "subtract": _ts("Apply as Subtract"),
        "intersect": _ts("Apply as Intersect"),
    }
    return labels.get(operation, operation)


def _session_event_payload(
    session: Sam3SelectionSession,
    overlay_model: Sam3SelectionOverlayModel,
    action: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": session.schema_version,
        "tool": TOOL_ID,
        "action": action,
        "candidateSetId": session.candidate_set.set_id,
        "selectedCandidateId": session.selected_candidate_id,
        "operation": session.operation,
        "availableCandidateIds": list(session.available_candidate_ids),
        "rejectedCandidateIds": list(session.rejected_candidate_ids),
        "visibleCandidateIds": [item.candidate_id for item in overlay_model.visible_items],
    }


def _ts(text: str) -> str:
    return translated_literal_domain(_TRANSLATION_DOMAIN, text, use_fallback=True)


def create_plugin() -> Sam3AISelectToolPlugin:
    return Sam3AISelectToolPlugin()


__all__ = [
    "PLUGIN_ID",
    "TOOL_ID",
    "Sam3AISelectToolPlugin",
    "Sam3AISelectToolStateError",
    "create_plugin",
]
