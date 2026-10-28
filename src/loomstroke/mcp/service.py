"""Protocol-independent LoomStroke MCP facade."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import inspect
import json
import re
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, unquote

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QImageReader,
    QPainter,
    QPen,
    QPolygonF,
)

from loomstroke.core.book_export_order import export_page_ids
from loomstroke.core.export_naming import (
    NamingContext,
    NamingError,
    detect_collisions,
    render_export_names,
    validate_template,
)
from loomstroke.io.atomic_export_writer import write_atomically
from loomstroke.io.layer_io import DocumentMetadata, DocumentSnapshot, LayerIO, LayerSnapshot
from loomstroke.ui.canvas.page_scene_snapshot import array_to_qimage as _shared_array_to_qimage
from loomstroke.ui.canvas.page_scene_snapshot import (
    flatten_render_layers as _shared_flatten_render_layers,
)
from loomstroke.ui.canvas.page_scene_snapshot import page_background as _shared_page_background
from loomstroke.ui.canvas.page_scene_snapshot import page_dimensions as _shared_page_dimensions
from loomstroke.ui.canvas.page_scene_snapshot import (
    render_layer_from_snapshot as _shared_render_layer_from_snapshot,
)
from loomstroke.ui.canvas.page_scene_snapshot import scene_from_page as _shared_scene_from_page
from loomstroke.ui.canvas.render_scene import (
    RenderLayerSnapshot,
    RenderSceneSnapshot,
)
from loomstroke.ui.canvas.wgpu_backend import WgpuDeviceManager, WgpuOffscreenRenderer
from loomstroke.ui.canvas.wgpu_backend.types import WgpuBackendError, WgpuUnavailableError

from . import production as pct
from . import production_runner
from .ai_effect_state import (
    apply_ai_effect_state_update,
    build_ai_effect_layer_state_schema,
    find_live_ai_effect_layer,
)
from .canvas_agent import CanvasAgentRuntime
from .editing_readiness import guard_existing_edit, known_document_closed
from .generation_capabilities import generation_submitter_capabilities
from .readiness import build_readiness_report
from .render_requirements import build_canvas_scene_snapshot, require_scene_text_renderer
from .safety import McpSafetyPolicy, scrub_secrets
from .sam3_segment_policies import validate_segment_policies
from .serialization import (
    detach_scene_snapshot,
    qimage_to_png_bytes,
    rgba_array_to_png_bytes,
    serialize_scene_snapshot,
    to_jsonable,
)
from .types import (
    PROTOCOL_VERSION,
    RESERVED_ASSET_KINDS,
    SERVER_NAME,
    SERVER_VERSION,
    AssetKind,
    AssetRecord,
    DocumentRecord,
    JobRecord,
    LayerRecord,
    McpStructuredError,
    PageRecord,
    ReasonCode,
)

_GENERATION_PAYLOAD_METADATA_KEYS = frozenset(
    {
        "binary",
        "bytes",
        "data",
        "fullresolutionimage",
        "full_resolution_image",
        "image",
        "mask",
        "pngbytes",
        "prompt",
        "providerrequestbody",
        "raw_lstroke",
        "rawbinary",
        "rawlstroke",
        "rawmask",
        "rawprompt",
        "requestbody",
        "payload",
    }
)

SnapshotProvider = Callable[[], RenderSceneSnapshot]
InvalidationProvider = Callable[[], Any]
GenerationSubmitter = Callable[
    [Mapping[str, Any]],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]
AIEffectPresetLister = Callable[[], Iterable[Any] | Awaitable[Iterable[Any]]]
AIEffectPresetApplier = Callable[[str], str | None | Awaitable[str | None]]
ComfyUIObjectInfoProvider = Callable[
    [Mapping[str, Any]],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]

_IMPORTABLE_RASTER_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_MAX_IMPORT_IMAGE_DIMENSION = 16384
_AI_EFFECT_LAYER_RETRY_AFTER_MS = 100
_SAM3_SAFE_ID_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_SAM3_APPLY_LAYER_MASK_RESULT_SCHEMA_VERSION = "sam3.apply_candidate_as_layer_mask_result.v1"
_SAM3_APPLY_SELECTION_RESULT_SCHEMA_VERSION = "sam3.apply_candidate_as_selection_result.v1"
_SAM3_CANCEL_RESULT_SCHEMA_VERSION = "sam3.cancel_job_result.v1"
_SAM3_EXPORT_MASK_PACKAGE_RESULT_SCHEMA_VERSION = "sam3.export_mask_package_result.v1"
_SAM3_OVERLAY_PREVIEW_RESULT_SCHEMA_VERSION = "sam3.overlay_preview_result.v1"
_SAM3_SEGMENT_CURRENT_RESULT_SCHEMA_VERSION = "sam3.segment_current_result.v1"
_SAM3_SEGMENT_RESULT_SCHEMA_VERSION = "sam3.segment_asset_result.v1"
_SAM3_APPLY_SELECTION_SOURCE = "sam3.mcp.apply_candidate_as_selection"


class LoomStrokeMcpService:
    """Transport-neutral implementation of LoomStroke MCP resources and tools."""

    _TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})

    def __init__(
        self,
        *,
        mode: str = "stdio",
        host_api: Any = None,
        canvas_service: Any = None,
        page_service: Any = None,
        settings: Any = None,
        roots: Sequence[str | Path] | None = None,
        confirmation_callback: Any = None,
        snapshot_provider: SnapshotProvider | None = None,
        invalidation_provider: InvalidationProvider | None = None,
        generation_submitter: GenerationSubmitter | None = None,
        generation_canceller: Callable[[str], bool] | None = None,
        canvas_agent_planner_provider: Any = None,
        ai_effect_preset_lister: AIEffectPresetLister | None = None,
        ai_effect_preset_applier: AIEffectPresetApplier | None = None,
        comfyui_object_info_provider: ComfyUIObjectInfoProvider | None = None,
        generation_history_store: Any = None,
        production_store_root: str | Path | None = None,
        sam3_surface: Any = None,
        sam3_provider: Any = None,
        tool_manager_provider: Callable[[], Any] | None = None,
        document_operation_handler: Any = None,
        document_state_provider: Callable[[], Mapping[str, Any]] | None = None,
        editing_services_provider: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.mode = mode
        self.host_api = host_api
        self.tool_manager_provider = tool_manager_provider
        self.document_operation_handler = document_operation_handler
        self.document_state_provider = document_state_provider
        self._editing_services_provider = editing_services_provider
        self._editing_lock = asyncio.Lock()
        self.canvas_service = canvas_service or getattr(host_api, "canvas", None)
        self.page_service = page_service or getattr(host_api, "pages", None)
        self.safety = McpSafetyPolicy(
            settings=settings if settings is not None else getattr(host_api, "settings", None),
            roots=roots,
            confirmation_callback=confirmation_callback,
        )
        self._snapshot_provider = snapshot_provider
        self._invalidation_provider = invalidation_provider
        self._generation_submitter = generation_submitter
        self._generation_canceller = generation_canceller
        self._canvas_agent_planner_provider = canvas_agent_planner_provider
        self._ai_effect_preset_lister = ai_effect_preset_lister
        self._ai_effect_preset_applier = ai_effect_preset_applier
        self._comfyui_object_info_provider = comfyui_object_info_provider
        self._generation_history_store = generation_history_store
        self._production_store = pct.ProductionControlStore(production_store_root)
        self._sam3_provider = sam3_provider
        self._sam3_surface = sam3_surface or _sam3_surface_from_provider(sam3_provider)
        self._production_qa_reports: dict[str, dict[str, Any]] = {}
        self._headless_documents: dict[str, DocumentSnapshot] = {}
        self._document_paths: dict[str, Path] = {}
        self._asset_records: dict[str, AssetRecord] = {}
        self._asset_payloads: dict[str, bytes] = {}
        self._jobs: dict[str, JobRecord] = {}
        self._job_tasks: dict[str, asyncio.Task[Any]] = {}
        self._wgpu_probe: dict[str, Any] | None = None
        self._graphic_plans: dict[str, dict[str, Any]] = {}
        self._canvas_agent = CanvasAgentRuntime(self)

    @classmethod
    def from_lstroke(
        cls,
        path: str | Path,
        *,
        roots: Sequence[str | Path] | None = None,
        strict: bool = True,
    ) -> LoomStrokeMcpService:
        service = cls(mode="stdio", roots=roots)
        service.load_lstroke(path, strict=strict)
        return service

    @property
    def sam3_provider(self) -> Any:
        """Currently attached SAM3 provider (None when detached)."""
        return self._sam3_provider

    def attach_sam3_provider(self, provider: Any) -> None:
        """Attach/replace the SAM3 provider after construction (None detaches).

        The SAM3 surface is always re-derived from the provider; an explicit
        ``sam3_surface`` passed to the constructor is overwritten by this call.
        """
        self._sam3_provider = provider
        self._sam3_surface = _sam3_surface_from_provider(provider)

    def load_lstroke(self, path: str | Path, *, strict: bool = True) -> str:
        source = Path(path).expanduser().resolve(strict=False)
        snapshot = LayerIO().load_document_snapshot(source, strict=strict)
        digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:12]
        document_id = f"doc_{digest}"
        self._headless_documents[document_id] = snapshot
        self._document_paths[document_id] = source
        return document_id

    # ------------------------------------------------------------------
    # Capability and document metadata
    def get_capabilities(self) -> dict[str, Any]:
        from .panel_tools import panel_edit_available  # noqa: PLC0415 - capability delegation

        wgpu_status = self._probe_wgpu_preview()
        readiness = self.get_readiness(wgpu_status=wgpu_status)
        editing = readiness["editing"]
        generation_ready = (
            generation_submitter_capabilities(self)["available"]
            and self.safety.external_services_allowed
        )
        file_writes_ready = bool(self.safety.roots)
        return {
            "protocol": PROTOCOL_VERSION,
            "server": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
            "mode": self.mode,
            "features": {
                "paintEditing": editing["canvasEditingAvailable"],
                "fillEditing": editing["canvasEditingAvailable"],
                "layerEditing": editing["canvasEditingAvailable"],
                "undoRedo": editing["canvasEditingAvailable"],
                "documentLifecycle": editing["documentWorkflowAvailable"],
                "brushLibrary": editing["brushLibraryAvailable"],
                "readOnlyInspection": True,
                "renderSceneSnapshot": self._has_scene_source(),
                "wgpuPreview": bool(wgpu_status["available"]),
                "glPreview": False,
                "generationJobs": generation_ready,
                "assetImport": file_writes_ready,
                "assetExport": file_writes_ready,
                "batchRender": file_writes_ready and self._has_scene_source(),
                "threeDAssets": editing["canvasEditingAvailable"],
                "graphicPlanning": True,
                "graphicExecution": self.canvas_service is not None
                and not known_document_closed(self),
                "mcpOnlyGraphicCompletion": file_writes_ready,
                "comicWorkflow": True,
                "comicPanelEditing": panel_edit_available(self),
                "canvasAgent": True,
                "canvasAgentReadOnlyLoop": self._has_scene_source(),
                "canvasAgentChangesetDraft": self._has_scene_source(),
                "canvasAgentPreviewOnly": self._has_scene_source(),
                "canvasAgentCommandBackedApply": (
                    self._has_scene_source() and self.canvas_service is not None
                ),
                "canvasAgentVerify": self._has_scene_source(),
            },
            "guards": {
                "externalServicesAllowed": self.safety.external_services_allowed,
                "confirmationPolicy": self.safety.confirmation_policy,
                "mutationRequiresUserConfirmation": self.safety.requires_confirmation(
                    "loomstroke_set_ai_effect_layer_state"
                ),
                "externalQueueRequiresUserConfirmation": self.safety.requires_confirmation(
                    "loomstroke_submit_generation"
                ),
                "rootsRequiredForFileWrites": True,
                "credentialReadOnDemandOnly": True,
                "localHttpDefaultDisabled": True,
                "localHttpBindHost": "127.0.0.1",
                "canvasWidgetDefaultBackend": "wgpu_native",
            },
            "environment": {
                "hasLiveCanvas": self.canvas_service is not None
                and not known_document_closed(self),
                "headlessDocumentCount": len(self._headless_documents),
                "roots": [str(root) for root in self.safety.roots],
            },
            "renderPreview": {
                "backend": "wgpu-offscreen",
                "status": scrub_secrets(wgpu_status),
            },
            "assetSchema": {
                "reservedKinds": list(RESERVED_ASSET_KINDS),
                "implementedKinds": [
                    AssetKind.RASTER.value,
                    AssetKind.GENERATED_OUTPUT.value,
                    AssetKind.SCENE3D.value,
                ],
            },
            "readiness": readiness,
        }

    def get_readiness(self, *, wgpu_status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return build_readiness_report(
            self,
            wgpu_status=wgpu_status or self._probe_wgpu_preview(),
        )

    def get_edit_context(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._canvas_agent.get_edit_context(arguments)

    def plan_canvas_edit(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._canvas_agent.plan_canvas_edit(arguments)

    def create_canvas_changeset(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._canvas_agent.create_canvas_changeset(arguments)

    def build_canvas_candidate_stack(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._canvas_agent.build_candidate_stack(arguments)

    def select_canvas_candidate(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._canvas_agent.select_candidate(arguments)

    def answer_canvas_clarification(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._canvas_agent.answer_canvas_clarification(arguments)

    def preview_canvas_changeset(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._canvas_agent.preview_canvas_changeset(arguments)

    async def apply_canvas_changeset(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        details = self._canvas_agent.canvas_changeset_apply_confirmation_details(arguments)
        await self._require_pct_confirmation(
            "loomstroke_apply_canvas_changeset",
            details,
            hard=True,
        )
        guard_existing_edit(self, "loomstroke_apply_canvas_changeset", {})
        return self._canvas_agent.apply_canvas_changeset(arguments)

    def verify_canvas_edit(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._canvas_agent.verify_canvas_edit(arguments)

    def canvas_agent_context_resource(self) -> dict[str, Any]:
        return self._canvas_agent.current_context_resource()

    def list_canvas_agent_runs(self) -> list[dict[str, Any]]:
        return self._canvas_agent.list_runs()

    def get_canvas_agent_run(self, run_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_run(run_id)

    def get_canvas_agent_run_plan(self, run_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_run_plan(run_id)

    def get_canvas_agent_run_clarification(self, run_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_run_clarification(run_id)

    def get_canvas_agent_run_clarification_answer(self, run_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_run_clarification_answer(run_id)

    def get_canvas_agent_run_changeset(self, run_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_run_changeset(run_id)

    def get_canvas_agent_run_preview(self, run_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_run_preview(run_id)

    def get_canvas_agent_run_apply_result(self, run_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_run_apply_result(run_id)

    def get_canvas_agent_changeset(self, changeset_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_changeset(changeset_id)

    def get_canvas_agent_preview(self, preview_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_preview(preview_id)

    def get_canvas_agent_apply_result(self, apply_id: str) -> dict[str, Any]:
        return self._canvas_agent.get_apply_result(apply_id)

    def list_production_recipes(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return production_runner.list_production_recipes(arguments)

    def get_production_recipe_detail(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        recipe_id = str(arguments.get("recipeId") or "").strip()
        return production_runner.get_production_recipe_detail(recipe_id)

    def validate_production_recipe_input(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return production_runner.validate_production_recipe_input(arguments)

    def plan_production_job(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return production_runner.plan_production_job(self, arguments)

    async def run_production_recipe(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return await production_runner.run_production_recipe(self, arguments)

    def score_artifact_candidates(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return production_runner.score_artifact_candidates(arguments)

    def create_rework_plan(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return production_runner.create_rework_plan(arguments)

    async def export_production_package(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        await self.safety.require_confirmation(
            "loomstroke_export_production_package",
            pct.scrub_for_pct(dict(arguments)),
        )
        return production_runner.export_production_package(self, arguments)

    def production_quality_profiles_resource(self) -> dict[str, Any]:
        return production_runner.quality_profiles_resource()

    def production_quality_profile_resource(self, profile_id: str) -> dict[str, Any]:
        return production_runner.quality_profile_resource(profile_id)

    def production_job_plan_resource(self, job_id: str) -> dict[str, Any]:
        return production_runner.production_job_plan_resource(self, job_id)

    def production_job_candidates_resource(self, job_id: str) -> dict[str, Any]:
        return production_runner.production_job_candidates_resource(self, job_id)

    def production_job_sam3_candidates_resource(self, job_id: str) -> dict[str, Any]:
        return production_runner.production_job_sam3_candidates_resource(self, job_id)

    def production_artifact_masks_resource(self, artifact_id: str) -> dict[str, Any]:
        return production_runner.production_artifact_masks_resource(self, artifact_id)

    def production_job_scorecard_resource(self, job_id: str) -> dict[str, Any]:
        return production_runner.production_job_scorecard_resource(self, job_id)

    def production_job_rework_resource(self, job_id: str) -> dict[str, Any]:
        return production_runner.production_job_rework_resource(self, job_id)

    def production_job_exports_resource(self, job_id: str) -> dict[str, Any]:
        return production_runner.production_job_exports_resource(self, job_id)

    def production_job_lineage_resource(self, job_id: str) -> dict[str, Any]:
        return production_runner.production_job_lineage_resource(self, job_id)

    def graphic_plan(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from .graphic_plan import GraphicPlanBuilder  # noqa: PLC0415

        plan = GraphicPlanBuilder(self).build(dict(arguments or {}))
        schema = plan.to_schema()
        self._graphic_plans[plan.plan_id] = schema
        return schema

    def plan_comic_page(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from .comic_plan import plan_comic_page  # noqa: PLC0415

        args = dict(arguments or {})
        known_asset_ids = [asset["assetId"] for asset in self.list_assets()]
        return plan_comic_page(
            args,
            default_page=self._default_comic_page(),
            known_asset_ids=known_asset_ids,
            roots=self.safety.roots,
        )

    def validate_comic_plan(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from .comic_plan import validate_comic_page_plan  # noqa: PLC0415

        args = dict(arguments or {})
        plan = args.get("plan")
        if not isinstance(plan, Mapping):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'plan' is required.",
                details={"argument": "plan"},
                code=-32602,
            )
        known_asset_ids = [asset["assetId"] for asset in self.list_assets()]
        validation = validate_comic_page_plan(
            plan,
            known_asset_ids=known_asset_ids,
            roots=self.safety.roots,
        )
        return validation.to_schema()

    async def create_from_request(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = dict(arguments or {})
        request = _first_nonempty_str(
            args,
            ("request", "prompt", "goal", "script", "story", "instructions"),
        )
        if request is None:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'request', 'prompt', 'goal', 'script', 'story', or 'instructions' is required.",
                details={"argument": "request"},
                code=-32602,
            )

        intent = _detect_create_intent(args, request)
        if intent == "comic_page":
            result = await self.create_comic_page(_generic_create_comic_args(args, request))
            return _generic_create_response(
                intent=intent,
                routed_tool="loomstroke_create_comic_page",
                result=result,
                completed_steps=["intent_route", *result.get("completedSteps", [])],
            )

        recipe_id = _recipe_id_for_create_intent(intent)
        if recipe_id is not None:
            plan = self.plan_production_job(
                {
                    "recipeId": recipe_id,
                    "mode": "dry_run",
                    "input": _generic_create_production_input(args, request),
                }
            )
            return _generic_create_response(
                intent=intent,
                routed_tool="loomstroke_plan_production_job",
                result=plan,
                completed_steps=["intent_route", "production_dry_run_plan"],
                blocked_reasons=[
                    {
                        "code": "production_submit_requires_confirmation",
                        "message": (
                            "A ProductionRecipe plan was created, but artifact generation "
                            "requires explicit submit confirmation and any required backend "
                            "readiness."
                        ),
                        "action": (
                            "Review the plan, then call loomstroke_run_production_recipe "
                            "with mode=submit from a confirmation-capable client."
                        ),
                    }
                ],
                next_actions=[
                    {
                        "kind": "submit_production_recipe",
                        "tool": "loomstroke_run_production_recipe",
                        "recipeId": recipe_id,
                    }
                ],
            )

        graphic = self.graphic_plan({"prompt": request, "dryRun": True})
        return _generic_create_response(
            intent=intent,
            routed_tool="loomstroke_graphic_plan",
            result=graphic,
            completed_steps=["intent_route", "graphic_plan"],
            blocked_reasons=[
                {
                    "code": "no_direct_creator_for_intent",
                    "message": (
                        "No one-shot creator is registered for this request yet; "
                        "a safe GraphicPlan was returned instead."
                    ),
                    "action": (
                        "Use a domain-specific creator such as loomstroke_create_comic_page "
                        "or a ProductionRecipe shortcut when available."
                    ),
                }
            ],
        )

    async def create_comic_page(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = dict(arguments or {})
        script = _comic_workflow_script(args)

        planned = self.plan_comic_page(_comic_workflow_plan_args(args, script))
        plan = planned.get("plan")
        if not isinstance(plan, Mapping):
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Comic planner did not return a plan.",
                details={"schemaVersion": planned.get("schemaVersion")},
                code=-32603,
            )

        validation = self.validate_comic_plan({"plan": plan})
        preview = await self.render_comic_preview(_comic_workflow_preview_args(args, plan))

        export_result: dict[str, Any] | None = None
        requested_export, output_directory = _comic_workflow_export_request(args)
        if requested_export:
            if not isinstance(output_directory, str) or not output_directory.strip():
                export_result = _blocked_comic_export_response(
                    code="output_directory_required",
                    message=("Package export was requested but outputDirectory was not provided."),
                    suggested_action="provide_output_directory_or_disable_export_package",
                )
            else:
                export_result = await self.export_comic_package(
                    _comic_workflow_export_args(args, plan, output_directory)
                )

        completed_steps = ["plan", "validate", "preview"]
        skipped_steps: list[str] = []
        if export_result is not None and not export_result.get("blocked"):
            completed_steps.append("export_package")
        elif export_result is not None:
            skipped_steps.append("export_package")

        next_actions = _collect_next_actions(preview, export_result or {})
        blocked_reasons = _collect_blocked_reasons(preview, export_result or {})
        return {
            "schemaVersion": "mcp.comic_page_workflow.v1",
            "ok": True,
            "blocked": bool(blocked_reasons),
            "blockedReasons": blocked_reasons,
            "mode": self.mode,
            "completedSteps": completed_steps,
            "skippedSteps": skipped_steps,
            "plan": plan,
            "planning": {
                "schemaVersion": planned.get("schemaVersion"),
                "warnings": planned.get("warnings", []),
            },
            "validation": validation,
            "preview": preview,
            "export": export_result,
            "nextActions": next_actions,
        }

    async def render_comic_preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from .comic_plan import validate_comic_page_plan  # noqa: PLC0415
        from .comic_preview import render_comic_preview_image  # noqa: PLC0415

        plan = arguments.get("plan")
        if not isinstance(plan, Mapping):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'plan' is required.",
                details={"argument": "plan"},
                code=-32602,
            )
        known_asset_ids = [asset["assetId"] for asset in self.list_assets()]
        validation = validate_comic_page_plan(
            plan,
            known_asset_ids=known_asset_ids,
            roots=self.safety.roots,
        )
        validation_schema = validation.to_schema()
        if validation.blocked:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "ComicPagePlan is invalid.",
                details={"validation": validation_schema},
                code=-32602,
            )

        output_path = arguments.get("outputPath")
        overwrite = bool(arguments.get("overwrite", False))
        register_asset = bool(arguments.get("registerAsset", True))
        target: Path | None = None
        file_write: dict[str, Any] = {
            "requested": output_path is not None,
            "performed": False,
            "blocked": False,
            "blockedReasons": [],
            "path": None,
        }
        extra_warnings: list[str] = []
        if output_path is not None:
            if (
                self.safety.requires_confirmation("loomstroke_render_comic_preview")
                and self.safety.confirmation_callback is None
            ):
                reason = {
                    "code": "confirmation_callback_unavailable",
                    "message": (
                        "File export requires user confirmation, but this MCP client "
                        "did not provide a confirmation callback."
                    ),
                    "action": "Retry without outputPath or use the live LoomStroke endpoint.",
                }
                file_write.update(
                    {
                        "blocked": True,
                        "blockedReasons": [reason],
                        "requestedPath": str(output_path),
                        "confirmationPolicy": self.safety.confirmation_policy,
                        "confirmationCallbackAvailable": False,
                        "suggestedAction": "omit_outputPath_for_asset_only_preview",
                    }
                )
                extra_warnings.append("file_write_blocked_confirmation_callback_unavailable")
            else:
                await self.safety.require_confirmation(
                    "loomstroke_render_comic_preview",
                    {
                        "outputPath": str(output_path),
                        "overwrite": overwrite,
                        "registerAsset": register_asset,
                    },
                )
                target = self.safety.validate_output_path(
                    str(output_path),
                    overwrite=overwrite,
                    allowed_suffixes=(".png",),
                )

        base_asset_id = arguments.get("baseAssetId")
        image, render_metadata = render_comic_preview_image(
            plan,
            asset_loader=self._qimage_from_asset,
            base_asset_id=(
                base_asset_id.strip()
                if isinstance(base_asset_id, str) and base_asset_id.strip()
                else None
            ),
        )
        png_bytes = qimage_to_png_bytes(image)
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            try:
                tmp.write_bytes(png_bytes)
                tmp.replace(target)
                file_write.update(
                    {
                        "performed": True,
                        "blocked": False,
                        "blockedReasons": [],
                        "path": str(target),
                    }
                )
            except Exception as exc:
                with contextlib.suppress(FileNotFoundError):
                    tmp.unlink()
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED,
                    "Comic preview export failed.",
                    details={"error": exc.__class__.__name__, "path": str(target)},
                ) from exc

        dimensions = {"width": int(image.width()), "height": int(image.height())}
        asset_id: str | None = None
        if register_asset:
            asset_id = self._register_generated_asset(
                png_bytes,
                source="comic_preview",
                dimensions=dimensions,
                metadata={
                    "schemaVersion": "mcp.comic_preview.v1",
                    "panelCount": validation_schema["summary"]["panelCount"],
                    "textBoxCount": validation_schema["summary"]["textBoxCount"],
                },
            )
        warnings = list(dict.fromkeys([*render_metadata["warnings"], *extra_warnings]))
        blocked_reasons = list(file_write.get("blockedReasons", []))
        return {
            "schemaVersion": "mcp.comic_preview.v1",
            "ok": True,
            "blocked": bool(blocked_reasons),
            "blockedReasons": blocked_reasons,
            "assetId": asset_id,
            "path": str(target) if target is not None else None,
            "fileWrite": file_write,
            "bytes": len(png_bytes),
            "dimensions": dimensions,
            "registered": asset_id is not None,
            "textRendered": bool(render_metadata["textRendered"]),
            "textRenderer": render_metadata.get("textRenderer"),
            "textFitReports": render_metadata["textFitReports"],
            "warnings": warnings,
            "nextActions": _blocked_file_write_next_actions() if blocked_reasons else [],
        }

    async def export_comic_package(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from .comic_plan import validate_comic_page_plan  # noqa: PLC0415
        from .comic_preview import render_comic_preview_image  # noqa: PLC0415

        plan = arguments.get("plan")
        if not isinstance(plan, Mapping):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'plan' is required.",
                details={"argument": "plan"},
                code=-32602,
            )
        output_directory = self._required_str(arguments, "outputDirectory")
        overwrite = bool(arguments.get("overwrite", False))
        register_asset = bool(arguments.get("registerAsset", True))
        base_asset_id = arguments.get("baseAssetId")
        base_asset = (
            base_asset_id.strip()
            if isinstance(base_asset_id, str) and base_asset_id.strip()
            else None
        )

        known_asset_ids = [asset["assetId"] for asset in self.list_assets()]
        validation = validate_comic_page_plan(
            plan,
            known_asset_ids=known_asset_ids,
            roots=self.safety.roots,
        )
        validation_schema = validation.to_schema()
        if validation.blocked:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "ComicPagePlan is invalid.",
                details={"validation": validation_schema},
                code=-32602,
            )

        export = plan.get("export") if isinstance(plan.get("export"), Mapping) else {}
        preview_file_name = _comic_package_file_name(
            arguments.get("fileName") or export.get("fileName") or "comic_preview.png",
            suffix=".png",
        )

        image, render_metadata = render_comic_preview_image(
            plan,
            asset_loader=self._qimage_from_asset,
            base_asset_id=base_asset,
        )
        png_bytes = qimage_to_png_bytes(image)
        dimensions = {"width": int(image.width()), "height": int(image.height())}
        warning_codes = [
            str(item.get("code"))
            for item in validation_schema.get("layoutWarnings", [])
            if isinstance(item, Mapping) and item.get("code")
        ]
        warnings = list(dict.fromkeys([*render_metadata["warnings"], *warning_codes]))

        if (
            self.safety.requires_confirmation("loomstroke_export_comic_package")
            and self.safety.confirmation_callback is None
        ):
            asset_id: str | None = None
            if register_asset:
                asset_id = self._register_generated_asset(
                    png_bytes,
                    source="comic_package_preview_fallback",
                    dimensions=dimensions,
                    metadata={
                        "schemaVersion": "mcp.comic_package.v1",
                        "panelCount": validation_schema["summary"]["panelCount"],
                        "textBoxCount": validation_schema["summary"]["textBoxCount"],
                    },
                )
            reason = {
                "code": "confirmation_callback_unavailable",
                "message": (
                    "Package export requires user confirmation, but this MCP client "
                    "did not provide a confirmation callback."
                ),
                "action": (
                    "Use the registered previewFallback asset, or retry from the live "
                    "LoomStroke endpoint to write files."
                ),
            }
            return {
                "schemaVersion": "mcp.comic_package.v1",
                "ok": True,
                "blocked": True,
                "blockedReasons": [reason],
                "packageDirectory": None,
                "requestedOutputDirectory": output_directory,
                "requestedFileName": preview_file_name,
                "assetId": asset_id,
                "registered": asset_id is not None,
                "dimensions": dimensions,
                "textRendered": bool(render_metadata["textRendered"]),
                "textRenderer": render_metadata.get("textRenderer"),
                "textFitReports": render_metadata["textFitReports"],
                "warnings": list(
                    dict.fromkeys(
                        [
                            *warnings,
                            "package_export_blocked_confirmation_callback_unavailable",
                        ]
                    )
                ),
                "files": [],
                "previewFallback": {
                    "schemaVersion": "mcp.comic_preview.v1",
                    "assetId": asset_id,
                    "path": None,
                    "bytes": len(png_bytes),
                    "dimensions": dimensions,
                    "registered": asset_id is not None,
                    "textRendered": bool(render_metadata["textRendered"]),
                    "textRenderer": render_metadata.get("textRenderer"),
                },
                "nextActions": _blocked_file_write_next_actions(),
            }

        output_base = Path(output_directory)
        targets = {
            "preview_png": self.safety.validate_output_path(
                output_base / preview_file_name,
                overwrite=overwrite,
                allowed_suffixes=(".png",),
            ),
            "comic_page_plan": self.safety.validate_output_path(
                output_base / "comic_page_plan.json",
                overwrite=overwrite,
                allowed_suffixes=(".json",),
            ),
            "text_fit_report": self.safety.validate_output_path(
                output_base / "text_fit_report.json",
                overwrite=overwrite,
                allowed_suffixes=(".json",),
            ),
            "package_manifest": self.safety.validate_output_path(
                output_base / "package_manifest.json",
                overwrite=overwrite,
                allowed_suffixes=(".json",),
            ),
        }

        await self.safety.require_confirmation(
            "loomstroke_export_comic_package",
            {
                "outputDirectory": output_directory,
                "fileCount": len(targets),
                "overwrite": overwrite,
                "registerAsset": register_asset,
                "panelCount": validation_schema["summary"]["panelCount"],
                "textBoxCount": validation_schema["summary"]["textBoxCount"],
            },
        )

        plan_payload = scrub_secrets(dict(plan))
        text_fit_payload = {
            "schemaVersion": "mcp.comic_text_fit_report.v1",
            "planSchemaVersion": validation_schema["planSchemaVersion"],
            "summary": validation_schema["summary"],
            "textFitReports": render_metadata["textFitReports"],
            "warnings": warnings,
        }
        package_files: list[dict[str, Any]] = [
            {
                "kind": "preview_png",
                "fileName": targets["preview_png"].name,
                "mimeType": "image/png",
                "bytes": len(png_bytes),
            },
            {
                "kind": "comic_page_plan",
                "fileName": targets["comic_page_plan"].name,
                "mimeType": "application/json",
                "bytes": _json_payload_size(plan_payload),
            },
            {
                "kind": "text_fit_report",
                "fileName": targets["text_fit_report"].name,
                "mimeType": "application/json",
                "bytes": _json_payload_size(text_fit_payload),
            },
        ]
        manifest_files = [
            *package_files,
            {
                "kind": "package_manifest",
                "fileName": targets["package_manifest"].name,
                "mimeType": "application/json",
                "bytes": 0,
            },
        ]
        manifest_payload = {
            "schemaVersion": "mcp.comic_package_manifest.v1",
            "createdAt": datetime.now(UTC).isoformat(),
            "dimensions": dimensions,
            "files": manifest_files,
            "textRendered": bool(render_metadata["textRendered"]),
            "textRenderer": render_metadata.get("textRenderer"),
            "credentialValuesIncluded": False,
            "binaryPayloadIncluded": False,
            "warnings": warnings,
        }
        manifest_files[-1]["bytes"] = _json_payload_size(manifest_payload)
        package_files = list(manifest_files)

        manifest_payload = {
            **manifest_payload,
            "files": package_files,
        }
        package_files[-1]["bytes"] = _json_payload_size(manifest_payload)
        manifest_payload["files"] = package_files

        _write_bytes_atomically(targets["preview_png"], png_bytes)
        _write_json_atomically(targets["comic_page_plan"], plan_payload)
        _write_json_atomically(targets["text_fit_report"], text_fit_payload)
        _write_json_atomically(targets["package_manifest"], manifest_payload)

        asset_id: str | None = None
        if register_asset:
            asset_id = self._register_generated_asset(
                png_bytes,
                source="comic_package_preview",
                dimensions=dimensions,
                metadata={
                    "schemaVersion": "mcp.comic_package.v1",
                    "panelCount": validation_schema["summary"]["panelCount"],
                    "textBoxCount": validation_schema["summary"]["textBoxCount"],
                },
            )

        return {
            "schemaVersion": "mcp.comic_package.v1",
            "ok": True,
            "blocked": False,
            "blockedReasons": [],
            "packageDirectory": str(targets["preview_png"].parent),
            "assetId": asset_id,
            "registered": asset_id is not None,
            "dimensions": dimensions,
            "textRendered": bool(render_metadata["textRendered"]),
            "textRenderer": render_metadata.get("textRenderer"),
            "textFitReports": render_metadata["textFitReports"],
            "warnings": warnings,
            "files": [
                {
                    **file,
                    "path": str(targets[str(file["kind"])]),
                }
                for file in package_files
            ],
            "nextActions": [],
        }

    async def create_comic_changeset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from .comic_changeset import build_comic_changeset_operations  # noqa: PLC0415
        from .comic_plan import validate_comic_page_plan  # noqa: PLC0415
        from .production_changeset_creation import _create_changeset  # noqa: PLC0415

        plan = arguments.get("plan")
        if not isinstance(plan, Mapping):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'plan' is required.",
                details={"argument": "plan"},
                code=-32602,
            )
        known_asset_ids = [asset["assetId"] for asset in self.list_assets()]
        validation = validate_comic_page_plan(
            plan,
            known_asset_ids=known_asset_ids,
            roots=self.safety.roots,
        )
        validation_schema = validation.to_schema()
        if validation.blocked:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "ComicPagePlan is invalid.",
                details={"validation": validation_schema},
                code=-32602,
            )

        build = build_comic_changeset_operations(plan)
        changeset = pct.create_changeset_record(
            {
                "name": str(arguments.get("name") or "Comic Page"),
                "target": {
                    "kind": "current_document",
                    "page": validation_schema["summary"].get("page"),
                    "sourceSchemaVersion": validation_schema["planSchemaVersion"],
                },
                "operations": build["operations"],
            }
        )
        changeset["metadata"] = {
            "schemaVersion": "mcp.comic_changeset.v1",
            "summary": build["summary"],
        }
        return await _create_changeset(
            self,
            changeset,
            confirmation_name="loomstroke_create_comic_changeset",
            summary=build["summary"],
            warnings=build["warnings"],
        )

    def list_documents(self) -> list[dict[str, Any]]:
        return [self._document_record_schema(record) for record in self._document_records()]

    def _document_records(self) -> list[DocumentRecord]:
        records: list[DocumentRecord] = []
        if self.canvas_service is not None and not known_document_closed(self):
            document_id = "current"
            name = "Current Document"
            current_document = getattr(self.canvas_service, "current_document", None)
            if callable(current_document):
                try:
                    ref = current_document()
                    document_id = str(getattr(ref, "id", document_id) or document_id)
                    name = str(getattr(ref, "name", None) or name)
                except Exception:
                    document_id = "current"
            records.append(
                DocumentRecord(
                    document_id=document_id,
                    name=name,
                    source="live",
                    active_page_id=self._active_page_id(),
                    metadata={"uri": "loomstroke://document/current"},
                )
            )
        for document_id, snapshot in self._headless_documents.items():
            path = self._document_paths.get(document_id)
            name = path.name if path is not None else document_id
            records.append(
                DocumentRecord(
                    document_id=document_id,
                    name=name,
                    source="headless",
                    path=str(path) if path is not None else None,
                    active_page_id=snapshot.pages[0].id if snapshot.pages else None,
                    metadata=self._metadata_schema(snapshot.metadata),
                )
            )
        return records

    def get_document(self, document_id: str) -> dict[str, Any]:
        if document_id in {"current", "document/current"}:
            record = self._live_document_record()
            if record is None:
                raise self._not_found("document", document_id)
            pages = self.list_pages(document_id=record.document_id)
            return {**self._document_record_schema(record), "pages": pages}
        snapshot = self._headless_documents.get(document_id)
        if snapshot is None:
            raise self._not_found("document", document_id)
        return {
            "documentId": document_id,
            "name": self._document_paths.get(document_id, Path(document_id)).name,
            "source": "headless",
            "path": str(self._document_paths.get(document_id, "")) or None,
            "metadata": self._metadata_schema(snapshot.metadata),
            "pages": self._headless_page_records(document_id, snapshot),
        }

    def _live_document_record(self) -> DocumentRecord | None:
        for record in self._document_records():
            if record.source == "live":
                return record
        return None

    # ------------------------------------------------------------------
    # Page and layer resources
    def list_pages(self, *, document_id: str | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if (
            self.page_service is not None
            and document_id in {None, "current"}
            and not known_document_closed(self)
        ):
            list_pages = getattr(self.page_service, "list_pages", None)
            if callable(list_pages):
                try:
                    for summary in list_pages():
                        records.append(self._page_summary_schema(summary, "current"))
                except Exception as exc:
                    raise McpStructuredError(
                        ReasonCode.BACKEND_FAILED,
                        "Failed to list live pages.",
                        details={"error": exc.__class__.__name__},
                    ) from exc
        for doc_id, snapshot in self._headless_documents.items():
            if document_id is not None and document_id != doc_id:
                continue
            records.extend(self._headless_page_records(doc_id, snapshot))
        return records

    def get_page(self, page_id: str) -> dict[str, Any]:
        for page in self.list_pages():
            if page.get("pageId") == page_id:
                return page
        raise self._not_found("page", page_id)

    def list_layers(self, *, page_id: str | None = None) -> list[dict[str, Any]]:
        records: list[LayerRecord] = []
        if self._has_scene_source() and page_id in {None, self._active_page_id()}:
            scene = self.get_scene_snapshot(raw=True)
            records.extend(
                self._scene_layer_records(
                    scene,
                    document_id="current",
                    page_id=self._active_page_id(),
                )
            )
        for document_id, snapshot in self._headless_documents.items():
            for page in snapshot.pages:
                if page_id is not None and page.id != page_id:
                    continue
                records.extend(self._page_layer_records(document_id, page.id, page.layers))
        return [self._layer_record_schema(record) for record in records]

    def get_layer(self, layer_id: str) -> dict[str, Any]:
        for layer in self.list_layers():
            if layer.get("layerId") == layer_id:
                return layer
        raise self._not_found("layer", layer_id)

    # ------------------------------------------------------------------
    # Scene snapshots and render preview
    def get_scene_snapshot(self, *, raw: bool = False) -> Any:
        scene = self._current_scene_snapshot()
        if raw:
            return scene
        return serialize_scene_snapshot(scene)

    def get_ai_effect_layer_state(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        layer_id = self._required_str(arguments, "layerId")
        include_gateway_file = bool(
            arguments.get("includeGatewayFile", arguments.get("include_gateway_file", False))
        )
        include_workflow = bool(
            arguments.get("includeWorkflow", arguments.get("include_workflow", False))
        )
        layer = self._find_ai_effect_render_layer(layer_id)
        return build_ai_effect_layer_state_schema(
            layer,
            include_gateway_file=include_gateway_file or include_workflow,
            include_workflow=include_workflow,
        )

    async def wait_layer_ready(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        layer_id = self._required_str(arguments, "layerId")
        timeout_ms = _bounded_int(
            arguments.get("timeoutMs"),
            default=5000,
            minimum=0,
            maximum=30000,
        )
        poll_interval_ms = _bounded_int(
            arguments.get("pollIntervalMs"),
            default=100,
            minimum=25,
            maximum=5000,
        )
        include_state = bool(arguments.get("includeState", True))
        return await self._wait_ai_effect_layer_ready(
            layer_id,
            timeout_ms=timeout_ms,
            poll_interval_ms=poll_interval_ms,
            include_state=include_state,
        )

    async def set_ai_effect_layer_state(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        layer_id = self._required_str(arguments, "layerId")
        await self.safety.require_confirmation(
            "loomstroke_set_ai_effect_layer_state",
            scrub_secrets(dict(arguments)),
        )
        guard_existing_edit(self, "loomstroke_set_ai_effect_layer_state", arguments)
        if self.canvas_service is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas service is not available.",
                details={"mode": self.mode},
            )
        update = apply_ai_effect_state_update(self.canvas_service, layer_id, arguments)
        layer = find_live_ai_effect_layer(self.canvas_service, layer_id)
        state = build_ai_effect_layer_state_schema(layer)
        return {"update": update, "state": state}

    def _current_scene_snapshot(self) -> RenderSceneSnapshot:
        if self._snapshot_provider is not None and not known_document_closed(self):
            return detach_scene_snapshot(self._snapshot_provider())
        if self.canvas_service is not None and not known_document_closed(self):
            return detach_scene_snapshot(build_canvas_scene_snapshot(self.canvas_service))
        for snapshot in self._headless_documents.values():
            if snapshot.pages:
                return self._scene_from_page(snapshot.metadata, snapshot.pages[0])
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "No render scene snapshot source is available.",
            details={"mode": self.mode},
        )

    def _find_ai_effect_render_layer(self, layer_id: str) -> RenderLayerSnapshot:
        scene = self._current_scene_snapshot()
        stack = list(scene.layers)
        while stack:
            layer = stack.pop()
            if layer.id == layer_id:
                if layer.layer_type != "ai_effect" or layer.ai_effect is None:
                    raise self._ai_effect_layer_not_found_error(layer_id, layer=layer)
                return layer
            stack.extend(layer.children)
        raise self._ai_effect_layer_not_found_error(layer_id, layer=None)

    def _ai_effect_layer_not_found_error(
        self,
        layer_id: str,
        *,
        layer: RenderLayerSnapshot | None,
    ) -> McpStructuredError:
        return McpStructuredError(
            ReasonCode.NOT_FOUND,
            "AI Effect layer was not found.",
            details=self._ai_effect_layer_required_details(layer_id, layer=layer),
        )

    def _ai_effect_layer_required_details(
        self,
        layer_id: str,
        *,
        layer: RenderLayerSnapshot | None,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {
            "layerId": layer_id,
            "ready": False,
            "stateAvailable": False,
            "retryAfterMs": _AI_EFFECT_LAYER_RETRY_AFTER_MS,
            "nextTool": "loomstroke_apply_ai_effect_preset",
            "waitTool": "loomstroke_wait_layer_ready",
            "submitExampleRef": "loomstroke_get_ai_effect_preset_detail.submitExample",
            "recommendedActions": [
                (
                    "Create an AI Effect layer first with "
                    "loomstroke_apply_ai_effect_preset, then pass that layerId to "
                    "loomstroke_submit_generation."
                ),
                (
                    "Use loomstroke_get_ai_effect_preset_detail to inspect the preset "
                    "submitExample before submitting generation."
                ),
            ],
        }
        if layer is not None:
            details["layerType"] = str(layer.layer_type or "")
            details["retryable"] = False
            details["reason"] = "layer_is_not_ai_effect"
        else:
            details["retryable"] = True
            details["reason"] = "layer_state_not_available"
        candidate_presets = self._candidate_ai_effect_presets()
        if candidate_presets:
            details["candidatePresets"] = candidate_presets
        return details

    def _candidate_ai_effect_presets(self, *, limit: int = 8) -> list[dict[str, Any]]:
        if self._ai_effect_preset_lister is None:
            return []
        if inspect.iscoroutinefunction(self._ai_effect_preset_lister):
            return []
        try:
            presets = self._ai_effect_preset_lister()
        except Exception:
            return []
        if inspect.isawaitable(presets):
            close = getattr(presets, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
            return []
        candidates: list[dict[str, Any]] = []
        for preset in presets or ():
            schema = self._ai_effect_preset_schema(preset)
            if schema.get("presetId"):
                candidates.append(schema)
            if len(candidates) >= limit:
                break
        return candidates

    def _ai_effect_layer_state_readiness(
        self,
        layer_id: str,
        *,
        include_state: bool = False,
    ) -> dict[str, Any]:
        try:
            layer = self._find_ai_effect_render_layer(layer_id)
        except McpStructuredError as exc:
            details = scrub_secrets(dict(exc.details))
            retryable = bool(
                details.get("retryable", exc.reason_code != ReasonCode.NOT_FOUND.value)
            )
            return {
                "schemaVersion": "mcp.layer_readiness.v1",
                "layerId": layer_id,
                "ready": False,
                "stateAvailable": False,
                "retryAfterMs": int(details.get("retryAfterMs") or _AI_EFFECT_LAYER_RETRY_AFTER_MS),
                "retryable": retryable,
                "reasonCode": exc.reason_code,
                "message": exc.message,
                "details": details,
                "recommendedActions": list(details.get("recommendedActions") or []),
            }
        response: dict[str, Any] = {
            "schemaVersion": "mcp.layer_readiness.v1",
            "layerId": layer_id,
            "ready": True,
            "stateAvailable": True,
            "retryAfterMs": 0,
            "retryable": False,
            "reasonCode": None,
            "message": None,
        }
        if include_state:
            response["state"] = build_ai_effect_layer_state_schema(layer)
        return response

    async def _wait_ai_effect_layer_ready(
        self,
        layer_id: str,
        *,
        timeout_ms: int,
        poll_interval_ms: int,
        include_state: bool,
    ) -> dict[str, Any]:
        started = time.monotonic()
        timeout_s = max(0.0, timeout_ms / 1000.0)
        poll_s = max(0.001, poll_interval_ms / 1000.0)
        readiness = self._ai_effect_layer_state_readiness(layer_id, include_state=include_state)
        while not readiness["ready"] and readiness.get("retryable", True):
            elapsed = time.monotonic() - started
            remaining = timeout_s - elapsed
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_s, remaining))
            readiness = self._ai_effect_layer_state_readiness(
                layer_id,
                include_state=include_state,
            )
        waited_ms = int(max(0.0, time.monotonic() - started) * 1000)
        readiness["timedOut"] = not readiness["ready"] and waited_ms >= timeout_ms
        readiness["waitedMs"] = waited_ms
        if not readiness["ready"] and "waitTool" not in readiness:
            readiness["waitTool"] = "loomstroke_wait_layer_ready"
        return readiness

    def render_preview(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        scene = self._resolve_scene_for_arguments(args)
        png_bytes, metadata = self._render_scene_to_png(scene)
        asset_id: str | None = None
        if bool(args.get("registerAsset", True)):
            asset_id = self._register_generated_asset(
                png_bytes,
                source="render_preview",
                dimensions={"width": int(scene.width), "height": int(scene.height)},
                metadata=metadata,
            )
        job = self._record_completed_job(
            "wgpu_render_preview",
            result_asset_ids=[asset_id] if asset_id else [],
            metadata=metadata,
        )
        response = {
            "mimeType": "image/png",
            "encoding": "base64",
            "data": _b64(png_bytes) if bool(args.get("includeData", True)) else None,
            "dimensions": {"width": int(scene.width), "height": int(scene.height)},
            "assetId": asset_id,
            "job": job.to_schema(),
            "backend": metadata.get("backend"),
            "warnings": metadata.get("warnings", []),
            "metrics": metadata.get("metrics", {}),
        }
        return response

    def _resolve_scene_for_arguments(self, args: Mapping[str, Any]) -> RenderSceneSnapshot:
        page_id = args.get("pageId")
        if isinstance(page_id, str) and page_id:
            for _, page, metadata in self._iter_page_snapshots():
                if page.id == page_id:
                    return self._scene_from_page(metadata, page)
            if page_id != self._active_page_id():
                raise self._not_found("page", page_id)
        return self._current_scene_snapshot()

    def _render_scene_to_png(
        self,
        scene: RenderSceneSnapshot,
    ) -> tuple[bytes, dict[str, Any]]:
        require_scene_text_renderer(scene)
        invalidation = None
        if self._invalidation_provider is not None:
            invalidation = self._invalidation_provider()
        renderer = None
        try:
            renderer = WgpuOffscreenRenderer()
            result = renderer.render_scene(scene, invalidation=invalidation)
            png_bytes = rgba_array_to_png_bytes(result.rgba)
            return png_bytes, {
                "backend": result.backend_name,
                "warnings": list(result.warnings),
                "metrics": to_jsonable(result.metrics),
            }
        except WgpuUnavailableError as exc:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "wgpu preview backend is unavailable.",
                details={"error": str(exc)},
            ) from exc
        except WgpuBackendError as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "wgpu preview render failed.",
                details={"error": exc.__class__.__name__},
            ) from exc
        except Exception as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Preview render failed.",
                details={"error": exc.__class__.__name__},
            ) from exc
        finally:
            if renderer is not None:
                renderer.release()

    # ------------------------------------------------------------------
    # Assets and export
    def list_assets(self) -> list[dict[str, Any]]:
        assets: dict[str, AssetRecord] = dict(self._asset_records)
        for asset in self._dynamic_layer_assets():
            assets[asset.asset_id] = asset
        return [
            record.to_schema() for record in sorted(assets.values(), key=lambda item: item.asset_id)
        ]

    def import_asset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        input_path = self._required_str(arguments, "inputPath")
        source = self.safety.validate_input_path(
            input_path,
            allowed_suffixes=_IMPORTABLE_RASTER_SUFFIXES,
        )
        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        probed_size = reader.size()
        if probed_size.isValid():
            self._validate_import_image_dimensions(
                int(probed_size.width()),
                int(probed_size.height()),
                source.name,
            )
        if not reader.canRead():
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Input asset image could not be decoded.",
                details={
                    "fileName": source.name,
                    "error": str(reader.errorString() or "unsupported_image"),
                },
                code=-32602,
            )
        image = reader.read()
        if image.isNull():
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Input asset image could not be decoded.",
                details={
                    "fileName": source.name,
                    "error": str(reader.errorString() or "empty_image"),
                },
                code=-32602,
            )
        self._validate_import_image_dimensions(
            int(image.width()),
            int(image.height()),
            source.name,
        )
        normalized = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        png_bytes = qimage_to_png_bytes(normalized)
        dimensions = {"width": int(normalized.width()), "height": int(normalized.height())}
        display_name = str(arguments.get("displayName") or source.stem).strip()[:256]
        original_format = _qimage_reader_format_name(reader)
        metadata = {
            "schemaVersion": "mcp.asset_import.v1",
            "fileName": source.name,
            "displayName": display_name or source.stem,
            "originalExtension": source.suffix.lower(),
            "originalFormat": original_format,
            "normalizedMimeType": "image/png",
        }
        asset_id = self._register_asset_payload(
            png_bytes,
            kind=AssetKind.RASTER.value,
            source="import_asset",
            dimensions=dimensions,
            metadata=metadata,
        )
        record = self._asset_record(asset_id)
        assert record is not None
        return {
            "schemaVersion": "mcp.asset_import.v1",
            "assetId": asset_id,
            "asset": record.to_schema(),
            "bytes": len(png_bytes),
            "dimensions": dimensions,
            "registered": True,
            "source": {
                "fileName": source.name,
                "originalExtension": source.suffix.lower(),
                "originalFormat": original_format,
            },
        }

    async def list_ai_effect_presets(self) -> dict[str, Any]:
        if self._ai_effect_preset_lister is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect preset library is not available.",
                details={"mode": self.mode},
            )
        presets = self._ai_effect_preset_lister()
        if inspect.isawaitable(presets):
            presets = await presets
        schemas = [self._ai_effect_preset_schema(preset) for preset in presets or ()]
        return {"presets": schemas, "presetCount": len(schemas)}

    async def apply_ai_effect_preset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preset_id = self._required_str(arguments, "presetId")
        wait_for_state_ms = _bounded_int(
            arguments.get("waitForStateMs"),
            default=500,
            minimum=0,
            maximum=5000,
        )
        await self.safety.require_confirmation(
            "loomstroke_apply_ai_effect_preset",
            {"presetId": preset_id, "waitForStateMs": wait_for_state_ms},
        )
        guard_existing_edit(self, "loomstroke_apply_ai_effect_preset", arguments)
        if self._ai_effect_preset_applier is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect preset applier is not available.",
                details={"mode": self.mode},
            )
        layer_id = self._ai_effect_preset_applier(preset_id)
        if inspect.isawaitable(layer_id):
            layer_id = await layer_id
        if not layer_id:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "AI Effect preset application failed.",
                details={"presetId": preset_id},
            )
        layer_id_str = str(layer_id)
        readiness = await self._wait_ai_effect_layer_ready(
            layer_id_str,
            timeout_ms=wait_for_state_ms,
            poll_interval_ms=50,
            include_state=False,
        )
        return {
            "presetId": preset_id,
            "layerId": layer_id_str,
            "ready": bool(readiness.get("ready")),
            "stateAvailable": bool(readiness.get("stateAvailable")),
            "retryAfterMs": int(readiness.get("retryAfterMs") or 0),
            "waitedMs": int(readiness.get("waitedMs") or 0),
            "waitTool": "loomstroke_wait_layer_ready",
            "readiness": readiness,
        }

    @staticmethod
    def _ai_effect_preset_schema(preset: Any) -> dict[str, Any]:
        def _value(name: str, default: Any = "") -> Any:
            if isinstance(preset, Mapping):
                return preset.get(name, default)
            return getattr(preset, name, default)

        manifest = _value("manifest", {})
        metadata = manifest.get("metadata", {}) if isinstance(manifest, Mapping) else {}
        name = _value("name") or (metadata.get("name", "") if isinstance(metadata, Mapping) else "")
        category = _value("category") or (
            metadata.get("category", "") if isinstance(metadata, Mapping) else ""
        )
        return {
            "presetId": str(_value("preset_id") or _value("presetId") or ""),
            "name": str(name or ""),
            "category": str(category or ""),
            "gatewayId": str(_value("gateway_id") or _value("gatewayId") or ""),
            "source": str(_value("source") or ""),
            "hasIcon": bool(_value("icon_image", None) or _value("iconImage", None)),
            "hasPreview": bool(_value("preview_image", None) or _value("previewImage", None)),
        }

    async def comfyui_object_info_summary(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .comfyui_workflow import ObjectInfoIndex  # noqa: PLC0415

        args = dict(arguments or {})
        object_info = await self._get_comfyui_object_info(args)
        return {
            **ObjectInfoIndex(object_info).summary(),
            "targetServerId": self._comfyui_target_server_id(args),
        }

    async def comfyui_search_nodes(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .comfyui_workflow import ObjectInfoIndex  # noqa: PLC0415

        args = dict(arguments or {})
        query = str(args.get("query") or "")
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        model_family = args.get("modelFamily") or args.get("model_family")
        object_info = await self._get_comfyui_object_info(args)
        return ObjectInfoIndex(object_info).search(
            query,
            limit=limit,
            model_family=str(model_family) if isinstance(model_family, str) else None,
        )

    async def comfyui_get_node_schema(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .comfyui_workflow import ObjectInfoIndex  # noqa: PLC0415

        args = dict(arguments or {})
        class_type = self._required_str(args, "classType")
        include_raw = bool(args.get("includeRaw", True))
        object_info = await self._get_comfyui_object_info(args)
        return ObjectInfoIndex(object_info).get_node_schema(class_type, include_raw=include_raw)

    async def comfyui_agent_catalog(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .comfyui_workflow import ObjectInfoIndex  # noqa: PLC0415

        args = dict(arguments or {})
        limit = _bounded_int(args.get("limit"), default=200, minimum=1, maximum=500)
        model_family = args.get("modelFamily") or args.get("model_family")
        role = args.get("role")
        object_info = await self._get_comfyui_object_info(args)
        result = ObjectInfoIndex(object_info).agent_catalog(
            include_nodes=bool(args.get("includeNodes", args.get("include_nodes", True))),
            include_raw=bool(args.get("includeRaw", args.get("include_raw", False))),
            role=str(role) if isinstance(role, str) else None,
            model_family=str(model_family) if isinstance(model_family, str) else None,
            query=str(args.get("query") or ""),
            limit=limit,
        )
        result["targetServerId"] = self._comfyui_target_server_id(args)
        return result

    async def comfyui_nodes_by_role(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .comfyui_workflow import ObjectInfoIndex  # noqa: PLC0415

        args = dict(arguments or {})
        role = self._required_str(args, "role")
        limit = _bounded_int(args.get("limit"), default=50, minimum=1, maximum=500)
        model_family = args.get("modelFamily") or args.get("model_family")
        object_info = await self._get_comfyui_object_info(args)
        result = ObjectInfoIndex(object_info).nodes_by_role(
            role,
            include_raw=bool(args.get("includeRaw", args.get("include_raw", False))),
            model_family=str(model_family) if isinstance(model_family, str) else None,
            query=str(args.get("query") or ""),
            limit=limit,
        )
        result["targetServerId"] = self._comfyui_target_server_id(args)
        return result

    async def comfyui_build_workflow_draft(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .comfyui_workflow import (  # noqa: PLC0415
            build_workflow_draft,
            request_from_arguments,
        )

        args = dict(arguments or {})
        object_info = await self._get_comfyui_object_info(args)
        try:
            return build_workflow_draft(object_info, request_from_arguments(args))
        except ValueError as exc:
            reason = (
                ReasonCode.CAPABILITY_UNAVAILABLE
                if str(exc).startswith("Missing required ComfyUI node")
                else ReasonCode.INVALID_ARGUMENT
            )
            raise McpStructuredError(
                reason,
                str(exc),
                details={"kind": args.get("kind", "sdxl_t2i")},
            ) from exc

    async def _get_comfyui_object_info(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.safety.require_external_services()
        provider = self._comfyui_object_info_provider
        if provider is not None:
            return await self._provided_comfyui_object_info(provider, arguments)
        registry = self._comfyui_registry()
        return await self._registry_comfyui_object_info(registry, arguments)

    async def _provided_comfyui_object_info(
        self,
        provider: ComfyUIObjectInfoProvider,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .comfyui_workflow import normalize_object_info  # noqa: PLC0415

        provided = provider(arguments)
        if inspect.isawaitable(provided):
            provided = await provided
        catalog = normalize_object_info(provided)
        return {"nodes": catalog.nodes, "models": catalog.models}

    def _comfyui_registry(self) -> Any:
        try:
            from loomstroke.plugins.comfyui.server_registry import get_registry  # noqa: PLC0415
        except Exception as exc:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "ComfyUI registry is not available.",
                details={"error": exc.__class__.__name__},
            ) from exc
        return get_registry()

    async def _registry_comfyui_object_info(
        self,
        registry: Any,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .comfyui_workflow import normalize_object_info  # noqa: PLC0415

        if not registry.list_all():
            from loomstroke.plugins.comfyui.server_registry import (  # noqa: PLC0415
                sync_registry_from_settings,
            )

            sync_registry_from_settings(self.safety.settings, registry)
        target_server_id = self._comfyui_target_server_id(arguments)
        force_refresh = bool(arguments.get("forceRefresh", arguments.get("force_refresh", False)))
        if not force_refresh:
            cached = registry.get_object_info_cache(target_server_id)
            catalog = normalize_object_info(cached)
            if catalog.nodes:
                return {"nodes": catalog.nodes, "models": catalog.models}

        try:
            connection = await registry.get_or_create_connection(target_server_id)
            await self._ensure_comfyui_connection(connection)
            object_info = await self._call_comfyui_object_info(connection)
            catalog = normalize_object_info(object_info)
            models = await self._call_comfyui_models(connection, fallback=dict(catalog.models))
            if not catalog.nodes:
                raise RuntimeError("empty object_info")
            wrapped = {"nodes": catalog.nodes, "models": models}
            registry.set_object_info_cache(target_server_id, wrapped)
            return wrapped
        except McpStructuredError:
            raise
        except Exception as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Failed to fetch ComfyUI object_info.",
                details={"targetServerId": target_server_id, "error": exc.__class__.__name__},
            ) from exc

    async def _ensure_comfyui_connection(self, connection: Any) -> None:
        status = getattr(connection, "status", None)
        if bool(getattr(status, "connected", False)):
            return
        connect = getattr(connection, "connect", None)
        if not callable(connect):
            raise RuntimeError("connection has no connect()")
        connected = connect()
        if inspect.isawaitable(connected):
            connected = await connected
        if not connected:
            raise RuntimeError(str(getattr(status, "error", "") or "connect failed"))

    async def _call_comfyui_object_info(self, connection: Any) -> Any:
        getter = getattr(connection, "get_object_info", None)
        if not callable(getter):
            raise RuntimeError("connection has no get_object_info()")
        object_info = getter()
        if inspect.isawaitable(object_info):
            object_info = await object_info
        return object_info

    @staticmethod
    async def _call_comfyui_models(connection: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
        get_models = getattr(connection, "get_models", None)
        if not callable(get_models):
            return fallback
        try:
            model_result = get_models()
            if inspect.isawaitable(model_result):
                model_result = await model_result
            if isinstance(model_result, Mapping):
                return dict(model_result)
        except Exception:
            pass
        return fallback

    def _comfyui_target_server_id(self, arguments: Mapping[str, Any]) -> str:
        explicit = arguments.get("targetServerId", arguments.get("target_server_id"))
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        if self._comfyui_object_info_provider is not None:
            return "local"
        try:
            from loomstroke.plugins.comfyui.server_registry import get_registry  # noqa: PLC0415

            default_id = get_registry().default_server_id
            if isinstance(default_id, str) and default_id.strip():
                return default_id.strip()
        except Exception:
            pass
        return "local"

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        normalized = unquote(asset_id)
        record = self._asset_record(normalized)
        if record is None:
            raise self._not_found("asset", normalized)
        return record.to_schema()

    async def export_asset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        asset_id = self._required_str(arguments, "assetId")
        output_path = self._required_str(arguments, "outputPath")
        overwrite = bool(arguments.get("overwrite", False))
        await self.safety.require_confirmation(
            "loomstroke_export_asset",
            {"assetId": asset_id, "outputPath": output_path, "overwrite": overwrite},
        )
        target = self.safety.validate_output_path(
            output_path,
            overwrite=overwrite,
            allowed_suffixes=(".png",),
        )
        record = self._asset_record(asset_id)
        if record is None:
            raise self._not_found("asset", asset_id)
        payload = self._asset_bytes(asset_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_bytes(payload)
            tmp.replace(target)
        except Exception as exc:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Asset export failed.",
                details={"error": exc.__class__.__name__, "path": str(target)},
            ) from exc
        return {"asset": record.to_schema(), "path": str(target), "bytes": len(payload)}

    def _asset_record(self, asset_id: str) -> AssetRecord | None:
        if asset_id in self._asset_records:
            return self._asset_records[asset_id]
        for asset in self._dynamic_layer_assets():
            if asset.asset_id == asset_id:
                return asset
        return None

    def _asset_bytes(self, asset_id: str) -> bytes:
        if asset_id in self._asset_payloads:
            return self._asset_payloads[asset_id]
        prefix = "layer:"
        if asset_id.startswith(prefix):
            _, _, layer_id = asset_id.split(":", 2)
            layer = self._find_render_layer(layer_id)
            if layer is None or layer.image is None or layer.image.isNull():
                raise self._not_found("asset", asset_id)
            return qimage_to_png_bytes(layer.image)
        raise self._not_found("asset", asset_id)

    def _qimage_from_asset(self, asset_id: str) -> QImage:
        if asset_id.startswith("layer:"):
            _, _, layer_id = asset_id.split(":", 2)
            layer = self._find_render_layer(layer_id)
            if layer is None or layer.image is None or layer.image.isNull():
                raise self._not_found("asset", asset_id)
            return layer.image.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        payload = self._asset_bytes(asset_id)
        image = QImage()
        if not image.loadFromData(payload, "PNG") or image.isNull():
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Asset image could not be decoded.",
                details={"assetId": asset_id},
                code=-32603,
            )
        return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @staticmethod
    def _draw_asset_overlays(
        image: QImage,
        overlays: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        if image.isNull():
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Asset image is empty.",
                details={},
                code=-32603,
            )
        text_fit_reports: list[dict[str, Any]] = []
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            for overlay in overlays:
                overlay_type = str(overlay.get("type") or "").strip().lower()
                rect = _overlay_rect(overlay)
                radius = _bounded_float(
                    overlay.get("radius"),
                    default=6.0,
                    minimum=0.0,
                    maximum=200.0,
                )
                stroke_width = _bounded_float(
                    overlay.get("strokeWidth"),
                    default=2.0,
                    minimum=0.0,
                    maximum=64.0,
                )
                if overlay_type == "rect":
                    _draw_overlay_rect(painter, rect, overlay, radius, stroke_width)
                elif overlay_type == "text_box":
                    text_fit_reports.append(
                        _draw_overlay_text_box(painter, rect, overlay, radius, stroke_width)
                    )
                else:
                    raise McpStructuredError(
                        ReasonCode.INVALID_ARGUMENT,
                        "Unsupported overlay type.",
                        details={"type": overlay_type},
                        code=-32602,
                    )
        finally:
            painter.end()
        return text_fit_reports

    @staticmethod
    def _draw_speech_bubble_overlays(  # noqa: PLR0915 - drawing setup is intentionally linear
        image: QImage,
        bubbles: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if image.isNull():
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Asset image is empty.",
                details={},
                code=-32603,
            )
        from .comic_preview import comic_text_font  # noqa: PLC0415
        from .comic_text_layout import (  # noqa: PLC0415
            LINE_HEIGHT_FACTOR,
            estimated_text_height,
            fit_text_box,
        )

        width = max(1, int(image.width()))
        height = max(1, int(image.height()))
        text_fit_reports: list[dict[str, Any]] = []
        bubble_summaries: list[dict[str, Any]] = []
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            for index, bubble in enumerate(bubbles):
                text = str(bubble.get("text") or "")[:2000]
                bubble_id = str(bubble.get("id") or f"bubble_{index + 1}")
                font_size = _bounded_int(
                    bubble.get("fontSize"),
                    default=28,
                    minimum=6,
                    maximum=256,
                )
                min_font_size = _bounded_int(
                    bubble.get("minFontSize"),
                    default=6,
                    minimum=6,
                    maximum=font_size,
                )
                safe_padding = _bounded_float(
                    bubble.get("safePadding", bubble.get("padding")),
                    default=max(16.0, font_size * 0.65),
                    minimum=2.0,
                    maximum=512.0,
                )
                max_width = _normalized_or_pixel(
                    bubble.get("maxWidth"),
                    base=width,
                    default=0.36,
                    minimum=48.0,
                    maximum=float(width),
                )
                text_width = max(1.0, max_width - safe_padding * 2.0)
                wrap_estimate = fit_text_box(
                    text,
                    width=text_width,
                    height=1_000_000.0,
                    font_size=font_size,
                    fit_mode="wrap",
                    min_font_size=min_font_size,
                )
                line_count = max(1, wrap_estimate.line_count)
                # Size the body with the fit engine's own height model plus half a
                # line of slack, so the fit report does not flag every bubble.
                body_height = min(
                    float(height),
                    max(
                        font_size * 2.2,
                        estimated_text_height(line_count, font_size)
                        + safe_padding * 2.0
                        + font_size * LINE_HEIGHT_FACTOR * 0.5,
                    ),
                )
                body_width = min(float(width), max_width)
                anchor = bubble.get("anchor")
                anchor_x = _bubble_anchor_coordinate(anchor, "x", width, default=0.5)
                anchor_y = _bubble_anchor_coordinate(anchor, "y", height, default=0.2)
                tail_direction = _normalize_tail_direction(bubble.get("tailDirection"))
                tail_length = (
                    0.0
                    if tail_direction == "none"
                    else min(
                        64.0,
                        max(18.0, font_size * 0.9),
                    )
                )
                body_rect = _bubble_body_rect(
                    anchor_x,
                    anchor_y,
                    body_width,
                    body_height,
                    image_width=float(width),
                    image_height=float(height),
                    tail_direction=tail_direction,
                    tail_length=tail_length,
                )
                radius = _bubble_radius(bubble, body_rect)
                # Opaque: at alpha 245 dark artwork bled through and the bubble
                # read as light grey instead of white.
                fill = _qcolor(bubble.get("fillColor"), QColor(255, 255, 255, 255))
                stroke = _qcolor(bubble.get("strokeColor"), QColor(16, 16, 16, 255))
                stroke_width = _bounded_float(
                    bubble.get("strokeWidth"),
                    default=3.0,
                    minimum=0.0,
                    maximum=64.0,
                )
                text_color = _qcolor(bubble.get("textColor"), QColor(16, 16, 16, 255))
                painter.setBrush(QBrush(fill))
                painter.setPen(
                    Qt.PenStyle.NoPen if stroke_width <= 0 else QPen(stroke, stroke_width)
                )
                tail_polygon = _bubble_tail_polygon(body_rect, tail_direction, tail_length)
                if tail_polygon is not None:
                    painter.drawPolygon(tail_polygon)
                painter.drawRoundedRect(body_rect, radius, radius)
                text_rect = body_rect.adjusted(
                    safe_padding,
                    safe_padding,
                    -safe_padding,
                    -safe_padding,
                )
                fit_mode = str(bubble.get("fitMode") or bubble.get("fit") or "shrink_to_fit")
                fit = fit_text_box(
                    text,
                    width=text_rect.width(),
                    height=text_rect.height(),
                    font_size=font_size,
                    fit_mode=fit_mode,
                    min_font_size=min_font_size,
                )
                if QGuiApplication.instance() is not None:
                    painter.setFont(comic_text_font(fit.font_size))
                    painter.setPen(QPen(text_color, 1))
                    painter.drawText(
                        text_rect,
                        Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                        text,
                    )
                report = fit.to_fit_report(
                    text_id=bubble_id,
                    role="speech_bubble",
                    panel_id="asset_overlay",
                    include_visual_risks=True,
                )
                report["bubbleIndex"] = index
                text_fit_reports.append(report)
                bubble_summaries.append(
                    {
                        "bubbleId": bubble_id,
                        "style": str(bubble.get("style") or "manga_round"),
                        "tailDirection": tail_direction,
                        "rect": _rect_schema(body_rect),
                        "safePadding": round(float(safe_padding), 3),
                    }
                )
        finally:
            painter.end()
        return text_fit_reports, bubble_summaries

    # ------------------------------------------------------------------
    # Job control and generation submit
    def list_jobs(self) -> list[dict[str, Any]]:
        return [
            job.to_schema() for job in sorted(self._jobs.values(), key=lambda item: item.created_at)
        ]

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise self._not_found("job", job_id)
        return job.to_schema()

    async def wait_job(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        job_id = self._required_str(arguments, "jobId")
        timeout_ms = _bounded_int(
            arguments.get("timeoutMs"),
            default=30000,
            minimum=0,
            maximum=300000,
        )
        poll_ms = _bounded_int(
            arguments.get("pollIntervalMs"),
            default=250,
            minimum=25,
            maximum=5000,
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + (timeout_ms / 1000)
        timed_out = False
        while True:
            job = self.get_job_status(job_id)
            terminal = str(job.get("status") or "") in self._TERMINAL_JOB_STATUSES
            now = loop.time()
            if terminal:
                break
            if timeout_ms == 0 or now >= deadline:
                timed_out = True
                break
            await asyncio.sleep(min(poll_ms / 1000, max(0.0, deadline - now)))
        waited_ms = int(max(0.0, loop.time() - started) * 1000)
        return {
            **job,
            "terminal": str(job.get("status") or "") in self._TERMINAL_JOB_STATUSES,
            "timedOut": timed_out,
            "waitedMs": waited_ms,
        }

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise self._not_found("job", job_id)
        if job.status in {"completed", "failed", "cancelled"}:
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "Job is already terminal.",
                details={"jobId": job_id, "status": job.status},
            )
        task = self._job_tasks.get(job_id)
        await self.safety.require_confirmation(
            "loomstroke_cancel_job",
            {"jobId": job_id, "status": job.status},
        )
        same_target = self._jobs.get(job_id) is job and self._job_tasks.get(job_id) is task
        terminal = job.status in self._TERMINAL_JOB_STATUSES
        if not same_target or terminal or (task is not None and task.done()):
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                (
                    "Job is already terminal."
                    if same_target and terminal
                    else "Job could not be cancelled."
                ),
                details={"jobId": job_id, "status": job.status},
            )
        cancelled = False
        if job.cancel is not None:
            cancelled = bool(job.cancel())
        elif self._generation_canceller is not None and job.related_layer_id:
            cancelled = bool(self._generation_canceller(job.related_layer_id))
        if task is not None and not task.done():
            task.cancel()
            cancelled = True
        if not cancelled:
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "Job could not be cancelled.",
                details={"jobId": job_id, "status": job.status},
            )
        job.status = "cancelled"
        job.progress = None
        job.touch()
        return job.to_schema()

    async def submit_generation(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.safety.require_external_services()
        submission = _generation_submission_summary(arguments)
        await self.safety.require_confirmation(
            "loomstroke_submit_generation",
            submission,
        )
        guard_existing_edit(self, "loomstroke_submit_generation", arguments)
        if not generation_submitter_capabilities(self)["available"]:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect generation submitter is not available.",
                details={"mode": self.mode},
            )
        layer_id = self._required_str(arguments, "layerId")
        job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex}",
            job_class="ai_effect_generation",
            status="queued",
            progress=0.0,
            related_layer_id=layer_id,
            metadata={
                "source": "mcp",
                "argumentKeys": sorted(arguments.keys()),
                "submission": submission,
            },
        )
        self._jobs[job.job_id] = job
        task = asyncio.create_task(self._run_generation_job(job, dict(arguments)))
        self._job_tasks[job.job_id] = task
        return job.to_schema()

    async def _run_generation_job(self, job: JobRecord, arguments: dict[str, Any]) -> None:
        submission = _job_submission_metadata(job)
        try:
            job.status = "running"
            job.progress = 0.0
            job.touch()
            if job.related_layer_id and self._has_scene_source():
                readiness = self._ai_effect_layer_state_readiness(job.related_layer_id)
                if not readiness["ready"] and not readiness.get("retryable", True):
                    details = dict(readiness.get("details") or {})
                    raise McpStructuredError(
                        ReasonCode.NOT_FOUND,
                        "AI Effect layer was not found.",
                        details=details,
                    )
                job.metadata = {
                    **job.metadata,
                    "layerReadiness": readiness,
                }
            assert self._generation_submitter is not None
            result = self._generation_submitter(arguments)
            if inspect.isawaitable(result):
                result = await result
            safe_result = scrub_secrets(dict(result or {}))
            for asset in safe_result.get("assets", []) if isinstance(safe_result, dict) else []:
                if not isinstance(asset, Mapping):
                    continue
                asset_id = str(
                    asset.get("assetId") or f"job:{job.job_id}:{len(job.result_asset_ids)}"
                )
                data = asset.get("pngBytes")
                png_bytes = bytes(data) if isinstance(data, bytes | bytearray) else None
                if png_bytes is not None:
                    self._asset_payloads[asset_id] = png_bytes
                dimensions = self._normalize_asset_dimensions(
                    (
                        asset.get("dimensions")
                        if isinstance(asset.get("dimensions"), Mapping)
                        else None
                    ),
                    png_bytes,
                )
                self._asset_records[asset_id] = AssetRecord(
                    asset_id=asset_id,
                    kind=AssetKind.GENERATED_OUTPUT.value,
                    source="generation",
                    mime_type="image/png",
                    dimensions=dimensions,
                    related_layer_id=job.related_layer_id,
                    metadata={
                        "jobId": job.job_id,
                        **_generation_asset_public_metadata(asset),
                    },
                )
                job.result_asset_ids.append(asset_id)
            job.status = str(safe_result.get("status") or "completed")
            if job.status == "success":
                job.status = "completed"
            job.progress = 1.0 if job.status == "completed" else job.progress
            job.metadata = {
                "source": "mcp",
                "submission": submission,
                "result": self._generation_result_metadata(
                    safe_result,
                    result_asset_ids=job.result_asset_ids,
                ),
            }
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.progress = None
        except McpStructuredError as exc:
            job.status = "failed"
            job.metadata = {
                "source": "mcp",
                "submission": submission,
                "reason_code": exc.reason_code,
                "message": exc.message,
                "details": scrub_secrets(dict(exc.details)),
            }
        except Exception as exc:
            job.status = "failed"
            job.metadata = {
                "source": "mcp",
                "submission": submission,
                "reason_code": ReasonCode.BACKEND_FAILED.value,
                "error": exc.__class__.__name__,
            }
        finally:
            job.touch()

    # ------------------------------------------------------------------
    # Batch render
    async def batch_render(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        output_directory = self._required_str(arguments, "outputDirectory")
        page_ids = arguments.get("pageIds")
        requested_page_ids = [str(item) for item in page_ids] if isinstance(page_ids, list) else []
        include_roles = self._optional_role_list(arguments, "includeRoles")
        exclude_roles = self._optional_role_list(arguments, "excludeRoles")
        overwrite = bool(arguments.get("overwrite", False))
        pattern = str(arguments.get("fileNamePattern") or "{page_id}.png")
        template = self._batch_render_template(pattern)
        await self.safety.require_confirmation(
            "loomstroke_batch_render",
            {
                "outputDirectory": output_directory,
                "pageIds": requested_page_ids,
                "includeRoles": sorted(include_roles) if include_roles is not None else None,
                "excludeRoles": sorted(exclude_roles) if exclude_roles is not None else None,
                "overwrite": overwrite,
            },
        )
        job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex}",
            job_class="batch_render",
            status="running",
            progress=0.0,
            metadata={"source": "mcp"},
        )
        self._jobs[job.job_id] = job
        scenes = list(
            self._batch_scenes(
                requested_page_ids,
                include_roles=include_roles,
                exclude_roles=exclude_roles,
            )
        )
        if not scenes:
            job.status = "failed"
            job.touch()
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "No pages are available for batch render.",
                details={"pageIds": requested_page_ids},
            )
        # CT-10 X5: names come from the shared allowlist parser (same tokens
        # and sanitizing as File > Export Pages), collisions are rejected
        # before any write, and each file lands atomically.
        file_names = self._batch_render_file_names(template, [page_id for page_id, _s in scenes])
        outputs: list[dict[str, Any]] = []
        for index, ((page_id, scene), file_name) in enumerate(zip(scenes, file_names, strict=True)):
            if job.status == "cancelled":
                raise McpStructuredError(
                    ReasonCode.CANCELLED,
                    "Batch render was cancelled.",
                    details={"jobId": job.job_id},
                )
            target = self.safety.validate_output_path(
                Path(output_directory) / file_name,
                overwrite=overwrite,
                allowed_suffixes=(".png",),
            )
            png_bytes, metadata = self._render_scene_to_png(scene)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(target, lambda tmp, data=png_bytes: tmp.write_bytes(data))
            asset_id = self._register_generated_asset(
                png_bytes,
                source="batch_render",
                dimensions={"width": scene.width, "height": scene.height},
                metadata={"pageId": page_id, **metadata},
            )
            job.result_asset_ids.append(asset_id)
            outputs.append(
                {
                    "pageId": page_id,
                    "path": str(target),
                    "assetId": asset_id,
                    "warnings": list(metadata.get("warnings", []) or []),
                }
            )
            job.progress = (index + 1) / len(scenes)
            job.touch()
        job.status = "completed"
        job.progress = 1.0
        job.touch()
        return {"job": job.to_schema(), "outputs": outputs}

    @staticmethod
    def _batch_render_template(pattern: str) -> str:
        """Map the legacy ``fileNamePattern`` onto the CT-10 X1 naming template.

        The legacy pattern carried its own ``.png`` and a 0-based ``{index}``;
        the shared template appends the extension and numbers ``{index}`` from
        1, so ``{index}`` is rewritten to ``{index0}`` to keep old callers'
        numbering.  Any other suffix is rejected (PNG-only contract).
        """
        text = str(pattern or "{page_id}.png")
        suffix = Path(text).suffix.lower()
        if suffix == ".png":
            text = text[: -len(Path(text).suffix)]
        elif suffix and "}" not in suffix:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "fileNamePattern must end with .png.",
                details={"fileNamePattern": pattern},
            )
        text = text.replace("{index}", "{index0}")
        try:
            validate_template(text)
        except NamingError as error:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                f"Invalid fileNamePattern: {error}",
                details={"fileNamePattern": pattern},
            ) from error
        return text

    @staticmethod
    def _batch_render_file_names(template: str, page_ids: Sequence[str]) -> list[str]:
        contexts = [
            NamingContext(
                index=index + 1,
                page_id=page_id,
                page_name=page_id,
                folio=None,
                role="body",
                document_name="document",
            )
            for index, page_id in enumerate(page_ids)
        ]
        try:
            names = list(render_export_names(template, contexts, zero_pad=0, extension="png"))
        except NamingError as error:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                f"Invalid fileNamePattern: {error}",
                details={"fileNamePattern": template},
            ) from error
        collisions = detect_collisions(names)
        if collisions:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "fileNamePattern produces duplicate file names.",
                details={"collisions": list(collisions)},
            )
        return names

    async def create_asset_overlay_preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        asset_id = self._required_str(arguments, "assetId")
        output_path = self._required_str(arguments, "outputPath")
        overwrite = bool(arguments.get("overwrite", False))
        register_asset = bool(arguments.get("registerAsset", True))
        raw_overlays = arguments.get("overlays", [])
        if not isinstance(raw_overlays, list):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'overlays' must be an array.",
                details={"argument": "overlays"},
                code=-32602,
            )
        overlays = [item for item in raw_overlays if isinstance(item, Mapping)]
        if len(overlays) != len(raw_overlays):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Each overlay must be an object.",
                details={"argument": "overlays"},
                code=-32602,
            )
        await self.safety.require_confirmation(
            "loomstroke_create_asset_overlay_preview",
            {
                "assetId": asset_id,
                "outputPath": output_path,
                "overwrite": overwrite,
                "registerAsset": register_asset,
                "overlayCount": len(overlays),
            },
        )
        target = self.safety.validate_output_path(
            output_path,
            overwrite=overwrite,
            allowed_suffixes=(".png",),
        )
        base = self._qimage_from_asset(asset_id)
        result = base.copy()
        text_requested = any(
            str(item.get("type") or "").strip().lower() == "text_box"
            and bool(str(item.get("text") or ""))
            for item in overlays
        )
        can_draw_text = not text_requested or QGuiApplication.instance() is not None
        text_fit_reports = self._draw_asset_overlays(result, overlays)
        png_bytes = qimage_to_png_bytes(result)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_bytes(png_bytes)
            tmp.replace(target)
        except Exception as exc:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Asset overlay preview export failed.",
                details={"error": exc.__class__.__name__, "path": str(target)},
            ) from exc

        dimensions = {"width": int(result.width()), "height": int(result.height())}
        new_asset_id: str | None = None
        if register_asset:
            new_asset_id = self._register_generated_asset(
                png_bytes,
                source="overlay_preview",
                dimensions=dimensions,
                metadata={"sourceAssetId": asset_id, "overlayCount": len(overlays)},
            )
        return {
            "schemaVersion": "mcp.asset_overlay_preview.v1",
            "sourceAssetId": asset_id,
            "assetId": new_asset_id,
            "path": str(target),
            "bytes": len(png_bytes),
            "dimensions": dimensions,
            "overlayCount": len(overlays),
            "registered": new_asset_id is not None,
            "textRendered": can_draw_text,
            "textFitReports": text_fit_reports,
            "warnings": ([] if can_draw_text else ["text_render_skipped_no_qgui_application"]),
        }

    async def create_speech_bubble_overlay(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        asset_id = self._required_str(arguments, "assetId")
        output_path = self._required_str(arguments, "outputPath")
        overwrite = bool(arguments.get("overwrite", False))
        register_asset = bool(arguments.get("registerAsset", True))
        raw_bubbles = arguments.get("bubbles", [])
        if not isinstance(raw_bubbles, list):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'bubbles' must be an array.",
                details={"argument": "bubbles"},
                code=-32602,
            )
        bubbles = [item for item in raw_bubbles if isinstance(item, Mapping)]
        if len(bubbles) != len(raw_bubbles):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Each bubble must be an object.",
                details={"argument": "bubbles"},
                code=-32602,
            )
        await self.safety.require_confirmation(
            "loomstroke_create_speech_bubble_overlay",
            {
                "assetId": asset_id,
                "outputPath": output_path,
                "overwrite": overwrite,
                "registerAsset": register_asset,
                "bubbleCount": len(bubbles),
            },
        )
        target = self.safety.validate_output_path(
            output_path,
            overwrite=overwrite,
            allowed_suffixes=(".png",),
        )
        base = self._qimage_from_asset(asset_id)
        result = base.copy()
        text_requested = any(bool(str(item.get("text") or "")) for item in bubbles)
        can_draw_text = not text_requested or QGuiApplication.instance() is not None
        text_fit_reports, bubble_summaries = self._draw_speech_bubble_overlays(result, bubbles)
        png_bytes = qimage_to_png_bytes(result)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_bytes(png_bytes)
            tmp.replace(target)
        except Exception as exc:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Speech bubble overlay export failed.",
                details={"error": exc.__class__.__name__, "path": str(target)},
            ) from exc

        dimensions = {"width": int(result.width()), "height": int(result.height())}
        new_asset_id: str | None = None
        if register_asset:
            new_asset_id = self._register_generated_asset(
                png_bytes,
                source="speech_bubble_overlay",
                dimensions=dimensions,
                metadata={"sourceAssetId": asset_id, "bubbleCount": len(bubbles)},
            )
        warnings = [] if can_draw_text else ["text_render_skipped_no_qgui_application"]
        visual_risk_warnings = sorted(
            {
                str(warning)
                for report in text_fit_reports
                for warning in report.get("visualRiskWarnings", [])
            }
        )
        return {
            "schemaVersion": "mcp.speech_bubble_overlay.v1",
            "sourceAssetId": asset_id,
            "assetId": new_asset_id,
            "path": str(target),
            "bytes": len(png_bytes),
            "dimensions": dimensions,
            "bubbleCount": len(bubbles),
            "registered": new_asset_id is not None,
            "textRendered": can_draw_text,
            "bubbleSummaries": bubble_summaries,
            "textFitReports": text_fit_reports,
            "visualRiskWarnings": visual_risk_warnings,
            "warnings": warnings,
        }

    def list_sam3_resources(self) -> list[dict[str, Any]]:
        if self._sam3_surface is None:
            return []
        return [dict(resource) for resource in self._sam3_surface.list_resources()]

    def read_sam3_resource(self, uri: str) -> dict[str, Any]:
        if self._sam3_surface is None:
            raise self._not_found("sam3_resource", uri)
        try:
            return dict(self._sam3_surface.read_resource(uri))
        except (KeyError, OSError, ValueError) as exc:
            raise self._not_found("sam3_resource", uri) from exc

    async def get_sam3_readiness_resource(self) -> dict[str, Any]:
        if self._sam3_provider is not None:
            from loomstroke.ai_selection import sam3_readiness_resource  # noqa: PLC0415

            provider, surface = self._sam3_provider, self._sam3_surface
            revision = surface.begin_readiness_update() if surface is not None else None
            readiness = await provider.get_readiness()
            if (
                provider is self._sam3_provider
                and surface is self._sam3_surface
                and surface is not None
            ):
                surface.update_readiness(readiness, revision=revision)
            return sam3_readiness_resource(readiness)
        return self.read_sam3_resource("loomstroke://sam3/readiness")

    async def segment_sam3_asset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        validate_segment_policies(arguments)
        if self._sam3_provider is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider is not available.",
                details={"mode": self.mode},
            )
        asset_store = getattr(self._sam3_provider, "asset_store", None)
        if asset_store is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider asset store is not available.",
                details={"mode": self.mode},
            )

        from loomstroke.ai_selection import (  # noqa: PLC0415
            AiSelectionWorkerUnavailable,
            Sam3SegmentRequest,
        )

        source_snapshot_id = self._required_str(arguments, "sourceSnapshotId")
        request_id = _sam3_request_id(arguments.get("requestId"))
        prompt = _sam3_prompt_from_arguments(arguments)
        try:
            source_ref = asset_store.load_source_snapshot(source_snapshot_id)
        except (OSError, ValueError) as exc:
            raise self._not_found("sam3_source_snapshot", source_snapshot_id) from exc

        request = Sam3SegmentRequest(
            request_id=request_id,
            source_ref=source_ref,
            prompt=prompt,
            max_candidates=_bounded_int(
                arguments.get("maxCandidates"),
                default=16,
                minimum=1,
                maximum=64,
            ),
            min_score=_bounded_float(
                arguments.get("minScore"),
                default=0.2,
                minimum=0.0,
                maximum=1.0,
            ),
            mask_resolution_policy=_sam3_optional_str(
                arguments.get("maskResolutionPolicy"),
                default="source",
            ),
            postprocess_policy=_sam3_mapping_argument(arguments, "postprocessPolicy"),
            metadata=_sam3_mapping_argument(arguments, "metadata"),
        )
        try:
            candidate_set = await self._sam3_provider.segment(request)
        except AiSelectionWorkerUnavailable as exc:
            return _sam3_segment_blocked_result(
                asset_store=asset_store,
                request_id=request_id,
                source_snapshot_id=source_snapshot_id,
                readiness=getattr(exc, "readiness", None),
            )
        except (OSError, ValueError) as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "SAM3 segment asset failed.",
                details={
                    "requestId": request_id,
                    "sourceSnapshotId": source_snapshot_id,
                },
            ) from exc

        return _sam3_segment_success_result(
            request_id=request_id,
            source_snapshot_id=source_snapshot_id,
            candidate_set=candidate_set,
        )

    async def segment_current_sam3(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        validate_segment_policies(arguments)
        if self._sam3_provider is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider is not available.",
                details={"mode": self.mode},
            )
        asset_store = getattr(self._sam3_provider, "asset_store", None)
        if asset_store is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider asset store is not available.",
                details={"mode": self.mode},
            )

        request_id = _sam3_request_id(arguments.get("requestId"))
        _sam3_prompt_from_arguments(arguments)

        try:
            scene = self._resolve_scene_for_arguments(arguments)
            png_bytes, render_metadata = self._render_scene_to_png(scene)
            page_id = _sam3_optional_str(
                arguments.get("pageId"),
                default=self._active_page_id() or "",
            )
            snapshot_metadata: dict[str, Any] = {
                "provider": "mcp",
                "source": "loomstroke_sam3_segment_current",
                "requestId": request_id,
                "renderBackend": str(render_metadata.get("backend") or ""),
                "pageId": page_id or None,
            }
            render_warnings = render_metadata.get("warnings")
            if isinstance(render_warnings, Sequence) and not isinstance(
                render_warnings, str | bytes
            ):
                snapshot_metadata["renderWarnings"] = list(render_warnings)[:8]
            caller_metadata = _sam3_mapping_argument(arguments, "metadata")
            if caller_metadata:
                snapshot_metadata["callerMetadata"] = caller_metadata
            source_ref = asset_store.save_source_snapshot(
                png_bytes,
                snapshot_id=f"mcp-sam3-current-{_sam3_safe_id(request_id, fallback='current')}",
                kind="visible_composite",
                width=int(scene.width),
                height=int(scene.height),
                dpi=_sam3_dpi_from_scene(scene),
                page_id=page_id or None,
                content_hash=hashlib.sha256(png_bytes).hexdigest(),
                metadata=snapshot_metadata,
            )
        except McpStructuredError:
            raise
        except (OSError, ValueError) as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "SAM3 current scene source snapshot creation failed.",
                details={"requestId": request_id},
            ) from exc

        segment_arguments = dict(arguments)
        segment_arguments["requestId"] = request_id
        segment_arguments["sourceSnapshotId"] = source_ref.snapshot_id
        result = await self.segment_sam3_asset(segment_arguments)
        return _sam3_segment_current_result(result, source_ref)

    async def create_sam3_overlay_preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._sam3_provider is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider is not available.",
                details={"mode": self.mode},
            )
        asset_store = getattr(self._sam3_provider, "asset_store", None)
        if asset_store is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider asset store is not available.",
                details={"mode": self.mode},
            )

        from loomstroke.ai_selection import (  # noqa: PLC0415
            OverlayOptions,
            Sam3OverlayPreviewUnavailable,
        )

        candidate_set_id = self._required_str(arguments, "candidateSetId")
        try:
            asset_store.load_candidate_set(candidate_set_id)
        except (OSError, ValueError) as exc:
            raise self._not_found("sam3_candidate_set", candidate_set_id) from exc

        options = OverlayOptions(
            opacity=_bounded_float(
                arguments.get("opacity"),
                default=0.45,
                minimum=0.0,
                maximum=1.0,
            ),
            color_mode=_sam3_overlay_color_mode(arguments.get("colorMode")),
            include_rejected=bool(arguments.get("includeRejected", False)),
        )
        try:
            preview_asset = await self._sam3_provider.create_overlay_preview(
                candidate_set_id,
                options,
            )
        except Sam3OverlayPreviewUnavailable as exc:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 overlay preview generation is not available.",
                details={"candidateSetId": candidate_set_id},
            ) from exc
        except (OSError, ValueError) as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "SAM3 overlay preview generation failed.",
                details={"candidateSetId": candidate_set_id},
            ) from exc

        return _sam3_overlay_preview_success_result(
            candidate_set_id=candidate_set_id,
            preview_asset=preview_asset,
        )

    async def apply_sam3_candidate_as_selection(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._sam3_provider is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider is not available.",
                details={"mode": self.mode},
            )
        asset_store = getattr(self._sam3_provider, "asset_store", None)
        if asset_store is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider asset store is not available.",
                details={"mode": self.mode},
            )
        if self.canvas_service is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas service is not available.",
                details={"mode": self.mode},
            )
        if not callable(getattr(self.canvas_service, "submit", None)):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas service command submit is not available.",
                details={"mode": self.mode},
            )

        from loomstroke.ai_selection import (  # noqa: PLC0415
            BlockedReason,
            Sam3SelectionApplyExecutionResult,
            apply_selection_plan,
            build_selection_apply_plan,
        )

        candidate_set_id = self._required_str(arguments, "candidateSetId")
        candidate_id = self._required_str(arguments, "candidateId")
        operation = _sam3_selection_apply_operation(arguments.get("operation"))
        try:
            candidate_set = asset_store.load_candidate_set(candidate_set_id)
        except (OSError, ValueError) as exc:
            raise self._not_found("sam3_candidate_set", candidate_set_id) from exc

        try:
            current_context = self._sam3_current_selection_context(candidate_set.source_ref)
            plan = build_selection_apply_plan(
                candidate_set,
                candidate_id,
                current_context,
                operation=operation,
            )
        except McpStructuredError:
            raise
        except (OSError, ValueError) as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "SAM3 selection apply plan creation failed.",
                details={"candidateSetId": candidate_set_id, "candidateId": candidate_id},
            ) from exc

        requested_page_id = _sam3_optional_str(arguments.get("pageId"), default="")
        active_page_id = current_context.page_id or ""
        if requested_page_id and active_page_id and requested_page_id != active_page_id:
            execution = Sam3SelectionApplyExecutionResult(
                plan=plan,
                status="blocked",
                submitted=False,
                command_source=_SAM3_APPLY_SELECTION_SOURCE,
                blocked_reasons=(
                    BlockedReason(
                        code="requested_page_not_active",
                        message="SAM3 selection apply target page is not the active page.",
                        action="Switch to the requested page before applying the candidate.",
                    ),
                ),
                warnings=tuple(plan.warnings),
            )
            return _sam3_apply_selection_result(execution)

        if not plan.ready:
            execution = apply_selection_plan(
                plan,
                asset_store,
                self.canvas_service,
                source=_SAM3_APPLY_SELECTION_SOURCE,
            )
            return _sam3_apply_selection_result(execution)

        await self.safety.require_confirmation(
            "loomstroke_sam3_apply_candidate_as_selection",
            _sam3_apply_selection_confirmation_details(
                plan,
                request_id=_sam3_optional_str(arguments.get("requestId"), default=""),
                page_id=active_page_id,
            ),
        )
        guard_existing_edit(self, "loomstroke_sam3_apply_candidate_as_selection", arguments)
        execution = apply_selection_plan(
            plan,
            asset_store,
            self.canvas_service,
            source=_SAM3_APPLY_SELECTION_SOURCE,
        )
        return _sam3_apply_selection_result(execution)

    async def apply_sam3_candidate_as_layer_mask(
        self,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        asset_store = self._require_sam3_provider_asset_store()
        canvas_service = self._require_canvas_command_submitter()

        from loomstroke.ai_selection import (  # noqa: PLC0415
            BlockedReason,
            Sam3LayerMaskCommandAdapterError,
            build_layer_mask_apply_command,
            build_selection_apply_plan,
        )

        candidate_set_id = self._required_str(arguments, "candidateSetId")
        candidate_id = self._required_str(arguments, "candidateId")
        target_layer_id = self._required_str(arguments, "targetLayerId")
        try:
            candidate_set = asset_store.load_candidate_set(candidate_set_id)
        except (OSError, ValueError) as exc:
            raise self._not_found("sam3_candidate_set", candidate_set_id) from exc

        try:
            scene = self._current_scene_snapshot()
            current_context = self._sam3_selection_context_from_scene(
                scene,
                candidate_set.source_ref,
            )
            plan = build_selection_apply_plan(
                candidate_set,
                candidate_id,
                current_context,
                operation="replace",
            )
        except McpStructuredError:
            raise
        except (OSError, ValueError) as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "SAM3 layer mask apply plan creation failed.",
                details={"candidateSetId": candidate_set_id, "candidateId": candidate_id},
            ) from exc

        blocked_reasons = list(plan.blocked_reasons)
        requested_page_id = _sam3_optional_str(arguments.get("pageId"), default="")
        active_page_id = current_context.page_id or ""
        if requested_page_id and active_page_id and requested_page_id != active_page_id:
            blocked_reasons.append(
                BlockedReason(
                    code="requested_page_not_active",
                    message="SAM3 layer mask apply target page is not the active page.",
                    action="Switch to the requested page before applying the candidate.",
                )
            )

        available_layer_ids = set(_sam3_scene_layer_ids(scene))
        if target_layer_id not in available_layer_ids:
            blocked_reasons.append(
                BlockedReason(
                    code="target_layer_missing",
                    message="SAM3 layer mask apply target layer is not available.",
                    action="Choose an existing layer before applying the candidate.",
                )
            )

        if blocked_reasons:
            return _sam3_apply_layer_mask_result(
                plan,
                target_layer_id=target_layer_id,
                status="blocked",
                submitted=False,
                blocked_reasons=blocked_reasons,
                warnings=plan.warnings,
            )

        try:
            command = build_layer_mask_apply_command(
                asset_store,
                cast(str, plan.mask_asset_id),
                layer_id=target_layer_id,
                canvas_width=int(scene.width),
                canvas_height=int(scene.height),
            )
        except Sam3LayerMaskCommandAdapterError as exc:
            return _sam3_apply_layer_mask_result(
                plan,
                target_layer_id=target_layer_id,
                status="blocked",
                submitted=False,
                blocked_reasons=(
                    BlockedReason(
                        code=exc.code,
                        message=str(exc),
                        action="Run AI Select again before applying the candidate.",
                    ),
                ),
                warnings=plan.warnings,
            )
        except FileNotFoundError:
            return _sam3_apply_layer_mask_result(
                plan,
                target_layer_id=target_layer_id,
                status="blocked",
                submitted=False,
                blocked_reasons=(
                    BlockedReason(
                        code="mask_asset_missing",
                        message="SAM3 mask asset file is not available.",
                        action="Run AI Select again before applying the candidate.",
                    ),
                ),
                warnings=plan.warnings,
            )

        await self.safety.require_confirmation(
            "loomstroke_sam3_apply_candidate_as_layer_mask",
            _sam3_apply_layer_mask_confirmation_details(
                plan,
                target_layer_id=target_layer_id,
                request_id=_sam3_optional_str(arguments.get("requestId"), default=""),
                page_id=active_page_id,
            ),
        )
        guard_existing_edit(self, "loomstroke_sam3_apply_candidate_as_layer_mask", arguments)
        try:
            submitted = bool(canvas_service.submit(command))
        except Exception as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "SAM3 layer mask command submit failed.",
                details={"candidateSetId": candidate_set_id, "candidateId": candidate_id},
            ) from exc
        if not submitted:
            return _sam3_apply_layer_mask_result(
                plan,
                target_layer_id=target_layer_id,
                status="blocked",
                submitted=False,
                blocked_reasons=(
                    BlockedReason(
                        code="layer_mask_apply_rejected",
                        message="The canvas rejected the SAM3 layer mask apply command.",
                        action="Inspect the current layer state and retry the apply operation.",
                    ),
                ),
                warnings=plan.warnings,
            )

        return _sam3_apply_layer_mask_result(
            plan,
            target_layer_id=target_layer_id,
            status="applied",
            submitted=True,
            blocked_reasons=(),
            warnings=plan.warnings,
        )

    async def export_sam3_mask_package(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        asset_store = self._require_sam3_provider_asset_store()

        from dataclasses import replace  # noqa: PLC0415

        from loomstroke.ai_selection import (  # noqa: PLC0415
            Sam3ComicMaskPackageError,
            Sam3ComicMaskPackageExportError,
            build_comic_mask_package_from_candidate_set,
            export_comic_mask_package,
        )

        candidate_set_id = self._required_str(arguments, "candidateSetId")
        output_directory = self._required_str(arguments, "outputDirectory")
        try:
            candidate_set = asset_store.load_candidate_set(candidate_set_id)
        except (OSError, ValueError) as exc:
            raise self._not_found("sam3_candidate_set", candidate_set_id) from exc

        candidate_ids = _sam3_string_sequence_argument(
            arguments.get("candidateIds"),
            "candidateIds",
        )
        candidates = _sam3_export_mask_package_candidates(candidate_set, candidate_ids)
        if not candidates:
            return _sam3_export_mask_package_blocked_result(
                candidate_set_id=candidate_set_id,
                blocked_reason_codes=("candidate_filter_empty",),
            )
        if candidate_ids is not None:
            candidate_set = replace(
                candidate_set,
                candidates=candidates,
                rejected_candidates=(),
            )

        preflight_block = _sam3_export_mask_package_output_block_code(
            self,
            output_directory,
        )
        if preflight_block is not None:
            return _sam3_export_mask_package_blocked_result(
                candidate_set_id=candidate_set_id,
                blocked_reason_codes=(preflight_block,),
                candidate_count=len(candidates),
            )

        category = _sam3_optional_str(arguments.get("category"), default="character")
        metadata_path = _sam3_optional_str(
            arguments.get("metadataPath"),
            default="comic_mask_package.json",
        )
        accepted_candidate_ids = _sam3_string_sequence_argument(
            arguments.get("acceptedCandidateIds"),
            "acceptedCandidateIds",
        )
        try:
            package = build_comic_mask_package_from_candidate_set(
                candidate_set,
                category,
                panel_bboxes=_sam3_mapping_argument(arguments, "panelBboxes"),
                category_by_candidate_id=_sam3_mapping_argument(arguments, "categoryByCandidateId"),
                accepted_candidate_ids=accepted_candidate_ids or (),
                package_id=_sam3_optional_str(arguments.get("packageId"), default="") or None,
                source_document_id=(
                    _sam3_optional_str(arguments.get("sourceDocumentId"), default="") or None
                ),
                source_page_id=_sam3_optional_str(arguments.get("sourcePageId"), default="")
                or None,
                metadata_path=metadata_path,
            )
            export_assets = _sam3_export_mask_package_asset_refs(
                asset_store,
                candidates,
                include_previews=bool(arguments.get("includePreviews", False)),
            )
            package = replace(package, export_assets=export_assets)
        except Sam3ComicMaskPackageError as exc:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "SAM3 mask package arguments are invalid.",
                details={"candidateSetId": candidate_set_id},
                code=-32602,
            ) from exc
        except _Sam3McpMaskPackageBlocked as exc:
            return _sam3_export_mask_package_blocked_result(
                candidate_set_id=candidate_set_id,
                blocked_reason_codes=(exc.code,),
                candidate_count=len(candidates),
            )

        await self.safety.require_confirmation(
            "loomstroke_sam3_export_mask_package",
            _sam3_export_mask_package_confirmation_details(
                candidate_set_id=candidate_set_id,
                package_id=package.package_id,
                output_directory=output_directory,
                candidate_count=len(candidates),
                asset_count=len(package.export_assets),
                request_id=_sam3_optional_str(arguments.get("requestId"), default=""),
            ),
        )
        try:
            export_result = export_comic_mask_package(
                package,
                asset_store,
                output_directory,
                allowed_roots=self.safety.roots,
            )
        except Sam3ComicMaskPackageExportError as exc:
            return _sam3_export_mask_package_blocked_result(
                candidate_set_id=candidate_set_id,
                blocked_reason_codes=(exc.code,),
                package_id=package.package_id,
                candidate_count=len(candidates),
                metadata_path=package.metadata_path,
            )
        except (OSError, ValueError) as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "SAM3 mask package export failed.",
                details={"candidateSetId": candidate_set_id},
            ) from exc

        return _sam3_export_mask_package_success_result(
            candidate_set_id=candidate_set_id,
            package_id=package.package_id,
            candidate_count=len(candidates),
            export_result=export_result,
        )

    async def cancel_sam3_job(self, job_id: str) -> dict[str, Any]:
        if self._sam3_provider is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider is not available.",
                details={"mode": self.mode},
            )
        asset_store = getattr(self._sam3_provider, "asset_store", None)
        if asset_store is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider asset store is not available.",
                details={"mode": self.mode},
            )
        try:
            existing_status = asset_store.load_job_status(job_id)
        except (OSError, ValueError) as exc:
            raise self._not_found("sam3_job", job_id) from exc

        try:
            cancelled = bool(await self._sam3_provider.cancel(job_id))
        except (OSError, ValueError) as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "SAM3 job cancellation failed.",
                details={"jobId": job_id},
            ) from exc
        if not cancelled:
            return _sam3_cancel_blocked_result(existing_status)
        try:
            cancelled_status = asset_store.load_job_status(job_id)
        except (OSError, ValueError):
            cancelled_status = existing_status
        return _sam3_cancel_success_result(cancelled_status)

    # ------------------------------------------------------------------
    # Production Control Tower
    def get_production_capabilities(self) -> dict[str, Any]:
        catalog = pct.build_feature_catalog(self)
        return {
            "schemaVersion": "pct.production_capabilities.v1",
            "store": self._production_store.describe(),
            "catalog": catalog,
        }

    def ai_effect_catalog_resource(self) -> dict[str, Any]:
        return pct.build_ai_effect_catalog(
            self._sync_ai_effect_presets_for_resource(),
            external_services_allowed=self.safety.external_services_allowed,
        )

    def ai_effect_preset_resource(self, preset_id: str) -> dict[str, Any]:
        detail = pct.build_ai_effect_preset_detail(
            self._sync_ai_effect_presets_for_resource(),
            preset_id,
            external_services_allowed=self.safety.external_services_allowed,
        )
        if detail is None:
            raise self._not_found("preset", preset_id)
        return detail

    def production_artifacts_resource(self) -> dict[str, Any]:
        return self._sync_production_graph()

    def get_production_artifact(self, artifact_id: str) -> dict[str, Any]:
        self._sync_production_graph()
        artifact = self._production_store.get_record("artifacts", "artifactId", artifact_id)
        if artifact is None:
            raise self._not_found("artifact", artifact_id)
        return artifact

    def get_production_lineage_resource(self, artifact_id: str) -> dict[str, Any]:
        self._sync_production_graph()
        lineage = pct.build_lineage(self._production_store, artifact_id)
        if lineage is None:
            raise self._not_found("artifact", artifact_id)
        return lineage

    def list_production_jobs_resource(self) -> dict[str, Any]:
        jobs = pct.merge_mcp_and_production_jobs(
            mcp_jobs=self.list_jobs(),
            production_jobs=self._production_store.read_records("jobs"),
        )
        return {"schemaVersion": "pct.production_jobs.v1", "jobs": jobs}

    def get_production_job_resource(self, job_id: str) -> dict[str, Any]:
        for job in self.list_production_jobs_resource()["jobs"]:
            if str(job.get("jobId") or "") == job_id:
                return job
        raise self._not_found("job", job_id)

    def get_bible_resource(self, scope: str, scope_id: str | None = None) -> dict[str, Any]:
        entries = self._production_store.list_bible_entries(scope, scope_id=scope_id)
        return {
            "schemaVersion": "pct.bible_resource.v1",
            "scope": scope,
            "scopeId": scope_id,
            "entries": entries,
        }

    def get_qa_resource(self, artifact_id: str) -> dict[str, Any]:
        report = self._production_qa_reports.get(artifact_id)
        if report is None:
            report = self._production_store.read_qa_report(artifact_id)
        if report is None:
            raise self._not_found("qa", artifact_id)
        return report

    def list_export_profiles_resource(self) -> dict[str, Any]:
        return {
            "schemaVersion": "pct.export_profiles.v1",
            "profiles": self._production_store.read_records("export_profiles"),
        }

    def list_recipes_resource(self) -> dict[str, Any]:
        return {
            "schemaVersion": "pct.recipes.v1",
            "recipes": self._production_store.list_recipes(),
        }

    def get_recipe_resource(self, recipe_id: str) -> dict[str, Any]:
        recipe = self._production_store.get_recipe(recipe_id)
        if recipe is None:
            raise self._not_found("recipe", recipe_id)
        return recipe

    async def get_feature_catalog(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del arguments
        return pct.pct_response(pct.build_feature_catalog(self))

    async def explain_feature(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        feature_id = self._required_str(arguments, "featureId")
        explanation = pct.explain_feature(self, feature_id)
        blocked = explanation.get("feature") is None
        return pct.pct_response(
            explanation,
            blocked=blocked,
            blocked_reasons=["feature_not_found"] if blocked else [],
        )

    async def get_ai_effect_catalog(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del arguments
        presets = await pct.collect_ai_effect_presets(self)
        return pct.pct_response(
            pct.build_ai_effect_catalog(
                presets,
                external_services_allowed=self.safety.external_services_allowed,
            )
        )

    async def get_ai_effect_preset_detail(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preset_id = self._required_str(arguments, "presetId")
        presets = await pct.collect_ai_effect_presets(self)
        detail = pct.build_ai_effect_preset_detail(
            presets,
            preset_id,
            external_services_allowed=self.safety.external_services_allowed,
        )
        if detail is None:
            raise self._not_found("preset", preset_id)
        return pct.pct_response(detail)

    async def get_production_lineage(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        artifact_id = self._required_str(arguments, "artifactId")
        return pct.pct_response(self.get_production_lineage_resource(artifact_id))

    async def run_visual_qa_preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        artifact_id = arguments.get("artifactId") or arguments.get("artifact_id")
        asset_id = arguments.get("assetId") or arguments.get("asset_id")
        artifact: dict[str, Any] | None = None
        if isinstance(artifact_id, str) and artifact_id.strip():
            artifact = self.get_production_artifact(artifact_id.strip())
        if artifact is None and not (isinstance(asset_id, str) and asset_id.strip()):
            return pct.pct_response(
                {"schemaVersion": "pct.visual_qa.v1"},
                blocked=True,
                blocked_reasons=["artifact_or_asset_required"],
            )
        report = pct.run_visual_qa_preview(
            artifact=artifact,
            asset_id=asset_id.strip() if isinstance(asset_id, str) and asset_id.strip() else None,
            asset_bytes=self._asset_bytes,
            export_profile=(
                arguments.get("exportProfile")
                if isinstance(arguments.get("exportProfile"), Mapping)
                else None
            ),
            bible_terms=[
                str(item)
                for item in arguments.get("bibleTerms", [])
                if isinstance(arguments.get("bibleTerms"), list)
            ],
        )
        if report.get("artifactId"):
            self._production_qa_reports[str(report["artifactId"])] = report
        return pct.pct_response(report, warnings=report.get("warnings", []))

    async def validate_export_profile(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        profile = self._profile_from_arguments(arguments)
        validation = pct.validate_export_profile(
            profile,
            validate_path=self._validate_pct_output_path,
            roots=self.safety.roots,
        )
        return pct.pct_response(
            validation,
            warnings=validation.get("warnings", []),
            blocked=bool(validation.get("blockedReasons")),
            blocked_reasons=validation.get("blockedReasons", []),
        )

    async def preview_bible_injection(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        prompt = self._required_str(arguments, "prompt")
        scopes = arguments.get("scopes")
        scope_ids = arguments.get("scopeIds") or arguments.get("scope_ids")
        preview = pct.preview_bible_injection(
            self._production_store,
            prompt=prompt,
            scopes=[str(item) for item in scopes] if isinstance(scopes, list) else None,
            scope_ids=[str(item) for item in scope_ids] if isinstance(scope_ids, list) else None,
        )
        return pct.pct_response(preview)

    async def create_production_job(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if (
            self._pct_external_job_requested(arguments)
            and not self.safety.external_services_allowed
        ):
            return pct.pct_response(
                {"schemaVersion": "pct.production_job.v1"},
                blocked=True,
                blocked_reasons=["external_services_disabled"],
            )
        job = pct.create_production_job_record(arguments)
        await self._require_pct_confirmation("loomstroke_create_production_job", job, hard=True)
        self._production_store.add_job(job)
        return pct.pct_response({"job": job}, requires_confirmation=True)

    async def mark_artifact_decision(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        decision = pct.create_decision_record(arguments)
        if not decision.get("artifactId"):
            return pct.pct_response(
                {"decision": decision},
                blocked=True,
                blocked_reasons=["artifact_id_required"],
            )
        await self._require_pct_confirmation(
            "loomstroke_mark_artifact_decision",
            decision,
            hard=True,
        )
        self._production_store.add_decision(decision)
        return pct.pct_response({"decision": decision}, requires_confirmation=True)

    async def add_bible_rule(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        entry = pct.create_bible_entry(arguments)
        if not entry.get("text"):
            return pct.pct_response(
                {"entry": entry},
                blocked=True,
                blocked_reasons=["bible_rule_text_required"],
            )
        await self._require_pct_confirmation("loomstroke_add_bible_rule", entry, hard=True)
        self._production_store.add_bible_entry(entry)
        return pct.pct_response({"entry": entry}, requires_confirmation=True)

    async def create_changeset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from .production_changeset_creation import _create_changeset  # noqa: PLC0415

        changeset = pct.create_changeset_record(arguments)
        if not changeset.get("operations"):
            return pct.pct_response(
                {"changeset": changeset},
                blocked=True,
                blocked_reasons=["changeset_operations_required"],
            )
        return await _create_changeset(
            self, changeset, confirmation_name="loomstroke_create_changeset"
        )

    async def apply_changeset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from .production_changeset_creation import _blocked_applied_comic  # noqa: PLC0415
        from .production_changeset_state import ChangesetState  # noqa: PLC0415
        from .production_record_commit import commit_records, reject_pending_apply  # noqa: PLC0415

        changeset_id = self._required_str(arguments, "changesetId")
        state = ChangesetState.capture(self, changeset_id)
        if state is None:
            raise self._not_found("changeset", changeset_id)
        changeset = state.snapshot()
        reject_pending_apply(changeset)
        blocked = _blocked_applied_comic(self, changeset)
        if blocked is not None:
            return blocked
        unsupported = self._unsupported_changeset_operations(changeset.get("operations"))
        if unsupported:
            return pct.pct_response(
                {"changeset": changeset, "unsupportedOperations": unsupported},
                blocked=True,
                blocked_reasons=["unsupported_command_backed_operation"],
            )
        if (
            self._has_comic_changeset_operations(changeset.get("operations"))
            and self.canvas_service is None
        ):
            return pct.pct_response(
                {"changeset": changeset},
                blocked=True,
                blocked_reasons=["canvas_service_unavailable"],
            )
        await self._require_pct_confirmation(
            "loomstroke_apply_changeset", state.snapshot(), hard=True
        )
        state.ensure_current()
        if self._has_comic_changeset_operations(
            changeset.get("operations")
        ) and known_document_closed(self):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE, "No editable document is open."
            )
        record_result = commit_records(self, state, changeset, restore=False)
        if record_result is not None:
            return record_result
        applied = self._apply_pct_changeset_operations(changeset)
        changeset["status"] = "applied"
        changeset["appliedAt"] = datetime.now(UTC).isoformat()
        changeset["updatedAt"] = changeset["appliedAt"]
        changeset["appliedRecords"] = applied
        changeset = state.write(changeset)
        return pct.pct_response(
            {"changeset": changeset, "appliedRecords": applied},
            requires_confirmation=True,
        )

    async def rollback_changeset(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from .production_changeset_state import ChangesetState  # noqa: PLC0415
        from .production_record_commit import commit_records, recover_records  # noqa: PLC0415

        changeset_id = self._required_str(arguments, "changesetId")
        state = ChangesetState.capture(self, changeset_id)
        if state is None:
            raise self._not_found("changeset", changeset_id)
        changeset = state.snapshot()
        recovered = await recover_records(self, state)
        if recovered is not None:
            return recovered
        if str(changeset.get("status") or "") != "applied":
            return pct.pct_response(
                {"changeset": changeset},
                blocked=True,
                blocked_reasons=["changeset_not_applied"],
            )
        if self._has_comic_changeset_operations(changeset.get("operations")):
            return pct.pct_response(
                {"changeset": changeset},
                blocked=True,
                blocked_reasons=["comic_changeset_rollback_via_canvas_undo"],
            )
        await self._require_pct_confirmation(
            "loomstroke_rollback_changeset", state.snapshot(), hard=True
        )
        state.ensure_current()
        record_result = commit_records(self, state, changeset, restore=True)
        if record_result is not None:
            return record_result
        rolled_back = self._rollback_pct_changeset_records(changeset)
        changeset["status"] = "rolled_back"
        changeset["rolledBackAt"] = datetime.now(UTC).isoformat()
        changeset["updatedAt"] = changeset["rolledBackAt"]
        changeset = state.write(changeset)
        return pct.pct_response(
            {"changeset": changeset, "rolledBackRecords": rolled_back},
            requires_confirmation=True,
        )

    async def create_export_preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        profile = self._profile_from_arguments(arguments)
        validation = pct.validate_export_profile(
            profile,
            validate_path=self._validate_pct_output_path,
            roots=self.safety.roots,
        )
        if validation.get("blockedReasons"):
            return pct.pct_response(
                validation,
                warnings=validation.get("warnings", []),
                blocked=True,
                blocked_reasons=validation.get("blockedReasons", []),
            )
        await self._require_pct_confirmation(
            "loomstroke_create_export_preview",
            validation,
            hard=True,
        )
        asset_id = arguments.get("assetId") or arguments.get("asset_id")
        artifact_id = arguments.get("artifactId") or arguments.get("artifact_id")
        artifact = (
            self.get_production_artifact(str(artifact_id))
            if isinstance(artifact_id, str) and artifact_id.strip()
            else None
        )
        asset = (
            self.get_asset(str(asset_id))
            if isinstance(asset_id, str) and asset_id.strip()
            else None
        )
        preview = pct.build_export_preview(validation["profile"], artifact=artifact, asset=asset)
        if bool(arguments.get("writeFile", False)):
            if asset is None or not isinstance(asset_id, str):
                return pct.pct_response(
                    preview,
                    blocked=True,
                    blocked_reasons=["asset_id_required_for_file_write"],
                )
            target_path = validation["profile"].get("outputPath")
            if not target_path:
                return pct.pct_response(
                    preview,
                    blocked=True,
                    blocked_reasons=["output_path_required_for_file_write"],
                )
            target = self.safety.validate_output_path(
                str(target_path),
                overwrite=bool(profile.get("overwrite", False)),
                allowed_suffixes=(".png",),
            )
            payload = self._asset_bytes(asset_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(payload)
            tmp.replace(target)
            preview["writePerformed"] = True
            preview["path"] = str(target)
            preview["bytes"] = len(payload)
        self._production_store.upsert_records(
            "export_profiles",
            [validation["profile"]],
            key="profileId",
        )
        return pct.pct_response(
            preview,
            warnings=validation.get("warnings", []),
            requires_confirmation=True,
        )

    async def save_recipe(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        recipe = arguments.get("recipe")
        payload = dict(recipe) if isinstance(recipe, Mapping) else dict(arguments)
        validation = pct.validate_recipe_payload(payload)
        if validation.get("blockedReasons"):
            return pct.pct_response(
                validation,
                warnings=validation.get("warnings", []),
                blocked=True,
                blocked_reasons=validation.get("blockedReasons", []),
            )
        await self._require_pct_confirmation("loomstroke_save_recipe", validation, hard=True)
        saved = self._production_store.save_recipe(validation["recipe"])
        return pct.pct_response(
            {"recipe": saved},
            warnings=validation.get("warnings", []),
            requires_confirmation=True,
        )

    # ------------------------------------------------------------------
    # Internals
    def _sync_production_graph(self) -> dict[str, Any]:
        history_store = self._production_history_store()
        return pct.project_generation_history(self._production_store, history_store)

    def _sync_ai_effect_presets_for_resource(self) -> list[Any]:
        if self._ai_effect_preset_lister is None:
            return []
        presets = self._ai_effect_preset_lister()
        if inspect.isawaitable(presets):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect preset library is asynchronous for this transport.",
                details={"resource": "loomstroke://ai-effects/catalog"},
            )
        return list(presets or ())

    def _production_history_store(self) -> Any:
        if self._generation_history_store is not None:
            return self._generation_history_store
        getter = getattr(self.host_api, "get_service", None)
        if callable(getter):
            try:
                return getter("generation_history")
            except Exception:
                return None
        return None

    async def _require_pct_confirmation(
        self,
        operation: str,
        details: Mapping[str, Any],
        *,
        hard: bool = False,
    ) -> None:
        safe_details = pct.scrub_for_pct(dict(details))
        if not hard:
            await self.safety.require_confirmation(operation, safe_details)
            return
        if self.safety.hard_confirmations_skipped:
            return
        callback = self.safety.confirmation_callback
        if callback is None:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "User confirmation is required.",
                details={"operation": operation},
            )
        result = callback(operation, safe_details)
        if inspect.isawaitable(result):
            result = await result
        if not bool(result):
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "User denied the requested operation.",
                details={"operation": operation},
            )

    def _validate_pct_output_path(
        self,
        path: str,
        overwrite: bool,
        suffixes: Sequence[str],
    ) -> Path:
        return self.safety.validate_output_path(
            path,
            overwrite=overwrite,
            allowed_suffixes=suffixes,
        )

    @staticmethod
    def _profile_from_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
        profile = arguments.get("profile")
        if isinstance(profile, Mapping):
            merged = dict(profile)
            for key in ("assetId", "artifactId", "writeFile"):
                if key in arguments:
                    merged[key] = arguments[key]
            return merged
        return dict(arguments)

    @staticmethod
    def _pct_external_job_requested(arguments: Mapping[str, Any]) -> bool:
        if bool(arguments.get("external", False)):
            return True
        text = " ".join(
            str(arguments.get(key) or "")
            for key in ("kind", "jobClass", "gatewayId", "serviceId", "tool")
        ).lower()
        return any(part in text for part in ("ai", "generation", "comfy", "nanobanana", "gptimage"))

    @staticmethod
    def _unsupported_changeset_operations(operations: Any) -> list[dict[str, Any]]:
        from .comic_changeset import COMIC_CHANGESET_OPERATION_KINDS  # noqa: PLC0415

        if not isinstance(operations, list):
            return [{"operation": "invalid", "reason": "operations_must_be_list"}]
        supported = {
            "mark_artifact_decision",
            "add_bible_rule",
            "noop",
            *COMIC_CHANGESET_OPERATION_KINDS,
        }
        unsupported: list[dict[str, Any]] = []
        for index, operation in enumerate(operations):
            if not isinstance(operation, Mapping):
                unsupported.append({"index": index, "reason": "operation_must_be_object"})
                continue
            kind = str(operation.get("kind") or operation.get("op") or "")
            if kind not in supported:
                unsupported.append({"index": index, "kind": kind or "<missing>"})
        return unsupported

    @staticmethod
    def _has_comic_changeset_operations(operations: Any) -> bool:
        from .comic_changeset import is_comic_changeset_operation  # noqa: PLC0415

        return any(
            is_comic_changeset_operation(operation) for operation in _list_values(operations)
        )

    def _apply_pct_changeset_operations(self, changeset: Mapping[str, Any]) -> list[dict[str, Any]]:
        from .production_record_changeset import apply_record_operations  # noqa: PLC0415

        applied: list[dict[str, Any]] = []
        operations = changeset.get("operations")
        operation_list = operations if isinstance(operations, list) else []
        comic_operations = [
            operation
            for operation in operation_list
            if isinstance(operation, Mapping) and self._has_comic_changeset_operations([operation])
        ]
        if comic_operations:
            applied.append(self._apply_comic_changeset_operations(comic_operations, changeset))
        applied.extend(apply_record_operations(self._production_store, operation_list))
        return applied

    def _apply_comic_changeset_operations(
        self,
        operations: Sequence[Mapping[str, Any]],
        changeset: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .comic_changeset import ComicApplyCommand  # noqa: PLC0415

        if self.canvas_service is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas service is not available.",
                details={"changesetId": changeset.get("changesetId")},
            )
        submit = getattr(self.canvas_service, "submit", None)
        if not callable(submit):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas service cannot submit commands.",
                details={"changesetId": changeset.get("changesetId")},
            )
        command = ComicApplyCommand(operations)
        if not bool(submit(command)):
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Comic changeset apply failed.",
                details={"changesetId": changeset.get("changesetId")},
            )
        return {
            "kind": "comic_page",
            "recordType": "canvas_history_command",
            "recordId": changeset.get("changesetId"),
            "operationCount": len(operations),
            "createdLayerIds": command.created_layer_ids,
            "vectorObjectIds": command.vector_object_ids,
            "operationRecords": command.applied_records,
        }

    def _rollback_pct_changeset_records(self, changeset: Mapping[str, Any]) -> list[dict[str, Any]]:
        from .production_record_changeset import rollback_record_operations  # noqa: PLC0415

        return rollback_record_operations(self._production_store, changeset.get("appliedRecords"))

    def _has_scene_source(self) -> bool:
        return (
            (self._snapshot_provider is not None or self.canvas_service is not None)
            and not known_document_closed(self)
        ) or any(snapshot.pages for snapshot in self._headless_documents.values())

    def _generation_result_metadata(
        self,
        result: Mapping[str, Any],
        *,
        result_asset_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        metadata, _ = _omit_generation_payloads(scrub_secrets(dict(result)))
        metadata = cast(dict[str, Any], metadata)
        assets = result.get("assets")
        if isinstance(assets, list):
            summarized: list[Any] = []
            for index, asset in enumerate(assets):
                if not isinstance(asset, Mapping):
                    summarized.append(asset)
                    continue
                item, payload_omitted = _omit_generation_payloads(scrub_secrets(dict(asset)))
                item = cast(dict[str, Any], item)
                asset_id = str(item.get("assetId") or "")
                if not asset_id:
                    asset_id = (
                        str(result_asset_ids[index])
                        if index < len(result_asset_ids)
                        else f"job_asset_{len(summarized)}"
                    )
                    item["assetId"] = asset_id
                record = self._asset_record(asset_id)
                if record is not None and record.dimensions is not None:
                    dimensions = item.get("dimensions")
                    width = 0
                    height = 0
                    if isinstance(dimensions, Mapping):
                        with contextlib.suppress(TypeError, ValueError):
                            width = int(dimensions.get("width") or 0)
                        with contextlib.suppress(TypeError, ValueError):
                            height = int(dimensions.get("height") or 0)
                    if width <= 0 or height <= 0:
                        item["dimensions"] = dict(record.dimensions)
                if payload_omitted:
                    item["payload"] = "<omitted>"
                summarized.append(item)
            metadata["assets"] = summarized
        result_assets: list[dict[str, Any]] = []
        for asset_id in result_asset_ids:
            record = self._asset_record(asset_id)
            if record is not None:
                result_assets.append(record.to_schema())
        if result_assets:
            metadata["resultAssets"] = result_assets
        return metadata

    def _probe_wgpu_preview(self) -> dict[str, Any]:
        if self._wgpu_probe is not None:
            return dict(self._wgpu_probe)
        try:
            manager = WgpuDeviceManager(power_preference="low-power")
            try:
                manager.get_bundle()
                self._wgpu_probe = {
                    "available": True,
                    "importable": True,
                    "reason": None,
                }
            except WgpuUnavailableError as exc:
                self._wgpu_probe = {
                    "available": False,
                    "importable": True,
                    "reason": str(exc),
                }
            finally:
                manager.release()
        except Exception as exc:
            self._wgpu_probe = {
                "available": False,
                "importable": False,
                "reason": exc.__class__.__name__,
            }
        return dict(self._wgpu_probe)

    def _active_page_id(self) -> str | None:
        getter = getattr(self.page_service, "get_active_page_id", None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:
                return None
        return None

    def _default_comic_page(self) -> dict[str, Any]:
        try:
            active_page_id = self._active_page_id()
            pages = self.list_pages()
        except Exception:
            return {}
        selected = None
        if active_page_id:
            selected = next((page for page in pages if page.get("pageId") == active_page_id), None)
        if selected is None and pages:
            selected = pages[0]
        dimensions = selected.get("dimensions") if isinstance(selected, Mapping) else None
        if not isinstance(dimensions, Mapping):
            return {}
        return {
            "width": dimensions.get("width"),
            "height": dimensions.get("height"),
            "dpi": 72,
            "background": "#F7F1E6",
        }

    @staticmethod
    def _metadata_schema(metadata: DocumentMetadata) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            scrub_secrets(
                {
                    "width": int(metadata.width),
                    "height": int(metadata.height),
                    "backgroundColor": tuple(metadata.background_color),
                    "dpi": tuple(float(value) for value in metadata.dpi),
                    "formatVersion": metadata.format_version,
                    "appVersion": metadata.app_version,
                    "created": metadata.created,
                    "modified": metadata.modified,
                    "metadata": metadata.metadata,
                }
            ),
        )

    @staticmethod
    def _document_record_schema(record: DocumentRecord) -> dict[str, Any]:
        return {
            "documentId": record.document_id,
            "name": record.name,
            "source": record.source,
            "path": record.path,
            "activePageId": record.active_page_id,
            "metadata": scrub_secrets(record.metadata),
        }

    @staticmethod
    def _page_record_schema(record: PageRecord) -> dict[str, Any]:
        return {
            "pageId": record.page_id,
            "documentId": record.document_id,
            "index": record.index,
            "name": record.name,
            "dimensions": dict(record.dimensions),
            "layerIds": list(record.layer_ids),
            "metadata": scrub_secrets(record.metadata),
        }

    @staticmethod
    def _layer_record_schema(record: LayerRecord) -> dict[str, Any]:
        return {
            "layerId": record.layer_id,
            "documentId": record.document_id,
            "pageId": record.page_id,
            "name": record.name,
            "layerType": record.layer_type,
            "visible": record.visible,
            "opacity": record.opacity,
            "textureVersion": record.texture_version,
            "parentId": record.parent_id,
            "bounds": record.bounds,
            "metadata": scrub_secrets(record.metadata),
        }

    def _page_summary_schema(self, summary: Any, document_id: str) -> dict[str, Any]:
        canvas = getattr(summary, "canvas", None)
        width = int(getattr(canvas, "width", 0) or 0)
        height = int(getattr(canvas, "height", 0) or 0)
        return {
            "pageId": str(getattr(summary, "id", "")),
            "documentId": document_id,
            "index": int(getattr(summary, "index", 0) or 0),
            "name": str(getattr(summary, "name", "") or ""),
            "dimensions": {"width": width, "height": height},
            "layerIds": [],
            "dirty": bool(getattr(summary, "dirty", False)),
            "thumbnailAvailable": bool(getattr(summary, "thumbnail_available", False)),
        }

    def _headless_page_records(
        self,
        document_id: str,
        snapshot: DocumentSnapshot,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index, page in enumerate(snapshot.pages):
            dimensions = self._page_dimensions(snapshot.metadata, page)
            records.append(
                self._page_record_schema(
                    PageRecord(
                        page_id=page.id,
                        document_id=document_id,
                        index=int(page.index if page.index is not None else index),
                        name=page.name or f"Page {index + 1}",
                        dimensions=dimensions,
                        layer_ids=tuple(layer.id for layer in self._iter_layers(page.layers)),
                        metadata=dict(page.metadata or {}),
                    )
                )
            )
        return records

    def _scene_layer_records(
        self,
        scene: RenderSceneSnapshot,
        *,
        document_id: str,
        page_id: str | None,
    ) -> list[LayerRecord]:
        records: list[LayerRecord] = []
        for layer in scene.flat_layers:
            records.append(
                LayerRecord(
                    layer_id=layer.id,
                    document_id=document_id,
                    page_id=page_id,
                    name=layer.name,
                    layer_type=layer.layer_type,
                    visible=layer.visible,
                    opacity=layer.opacity,
                    texture_version=layer.texture_version,
                    parent_id=layer.parent_id,
                    bounds=self._layer_bounds(layer),
                    metadata={
                        "vectorKind": layer.vector_kind,
                        "locked": layer.locked,
                        "alphaLock": layer.alpha_lock,
                    },
                )
            )
        return records

    def _page_layer_records(
        self,
        document_id: str,
        page_id: str,
        layers: Sequence[LayerSnapshot],
        parent_id: str | None = None,
    ) -> list[LayerRecord]:
        records: list[LayerRecord] = []
        for layer in layers:
            records.append(
                LayerRecord(
                    layer_id=layer.id,
                    document_id=document_id,
                    page_id=page_id,
                    name=layer.name,
                    layer_type=layer.layer_type,
                    visible=layer.visible,
                    opacity=float(layer.opacity),
                    texture_version=0,
                    parent_id=parent_id,
                    bounds=layer.bounds,
                    metadata={
                        "vectorKind": layer.vector_kind,
                        "locked": layer.locked,
                        "alphaLock": layer.alpha_lock,
                    },
                )
            )
            records.extend(self._page_layer_records(document_id, page_id, layer.children, layer.id))
        return records

    def _batch_scenes(
        self,
        requested_page_ids: Sequence[str],
        *,
        include_roles: Collection[str] | None = None,
        exclude_roles: Collection[str] | None = None,
    ) -> Iterable[tuple[str, RenderSceneSnapshot]]:
        requested = set(requested_page_ids)
        yielded = False
        filtering_roles = include_roles is not None or exclude_roles is not None
        saw_pages = False
        for _document_id, pages, metadata in self._grouped_page_snapshots():
            saw_pages = saw_pages or bool(pages)
            # Role selection runs through the shared export-order provider
            # (CT-06 B5), per document so page ids never collide across the
            # live document and headless ones.  Order stays logical: binding
            # never reverses the export sequence (spec §4).
            allowed = set(
                export_page_ids(
                    [page.id for page in pages],
                    {page.id: getattr(page, "metadata", None) for page in pages},
                    include_roles=include_roles,
                    exclude_roles=exclude_roles,
                )
            )
            for page in pages:
                if requested and page.id not in requested:
                    continue
                if page.id not in allowed:
                    continue
                yielded = True
                yield page.id, self._scene_from_page(metadata, page)
        if not yielded and self._has_scene_source():
            # The live-canvas fallback exists for "there are no pages", not
            # for "the filter removed them all".  It has no role metadata,
            # so rendering it after a role filter emptied the selection
            # would silently export the very content the caller excluded.
            if filtering_roles and saw_pages:
                return
            page_id = self._active_page_id() or "current"
            if not requested or page_id in requested:
                yield page_id, self._current_scene_snapshot()

    def _grouped_page_snapshots(
        self,
    ) -> Iterable[tuple[str, list[Any], DocumentMetadata]]:
        current: tuple[str, DocumentMetadata] | None = None
        pages: list[Any] = []
        for document_id, page, metadata in self._iter_page_snapshots():
            if current is not None and document_id != current[0]:
                yield current[0], pages, current[1]
                pages = []
            current = (document_id, metadata)
            pages.append(page)
        if current is not None and pages:
            yield current[0], pages, current[1]

    @staticmethod
    def _optional_role_list(
        arguments: Mapping[str, Any],
        key: str,
    ) -> list[str] | None:
        value = arguments.get(key)
        if not isinstance(value, list):
            return None
        return [str(item) for item in value]

    def _iter_page_snapshots(self) -> Iterable[tuple[str, Any, DocumentMetadata]]:
        live = self._live_document_snapshot()
        if live is not None:
            for page in live.pages:
                yield "current", page, live.metadata
        for document_id, snapshot in self._headless_documents.items():
            for page in snapshot.pages:
                yield document_id, page, snapshot.metadata

    def _live_document_snapshot(self) -> DocumentSnapshot | None:
        canvas = self.canvas_service
        if canvas is None:
            return None
        get_layers = getattr(canvas, "get_document_layer_snapshots", None)
        if not callable(get_layers):
            return None
        state = getattr(canvas, "get_canvas_state_snapshot", lambda: None)()
        if state is None:
            return None
        metadata = DocumentMetadata(
            width=int(state.width),
            height=int(state.height),
            background_color=(
                int(state.background.red()),
                int(state.background.green()),
                int(state.background.blue()),
                int(state.background.alpha()),
            ),
            dpi=(float(getattr(state, "dpi", 72)), float(getattr(state, "dpi", 72))),
            format_version="1.2.0",
        )
        active_layers = list(get_layers())
        builder = getattr(self.page_service, "build_pages_for_save", None)
        if callable(builder):
            pages, _active_id = builder(active_layers, metadata)
        else:
            pages = []
        if not pages:
            pages = []
        return DocumentSnapshot(metadata=metadata, pages=list(pages))

    @staticmethod
    def _scene_from_page(metadata: DocumentMetadata, page: Any) -> RenderSceneSnapshot:
        # CT-10 X2: shared with the GUI multi-page export.
        return _shared_scene_from_page(metadata, page)

    @staticmethod
    def _render_layer_from_snapshot(
        snapshot: LayerSnapshot,
        *,
        parent_id: str | None = None,
    ) -> RenderLayerSnapshot:
        return _shared_render_layer_from_snapshot(snapshot, parent_id=parent_id)

    @staticmethod
    def _page_dimensions(metadata: DocumentMetadata, page: Any) -> dict[str, int]:
        return _shared_page_dimensions(metadata, page)

    @staticmethod
    def _page_background(metadata: DocumentMetadata, page: Any) -> tuple[int, int, int, int]:
        return _shared_page_background(metadata, page)

    @staticmethod
    def _iter_layers(layers: Sequence[LayerSnapshot]) -> Iterable[LayerSnapshot]:
        for layer in layers:
            yield layer
            yield from LoomStrokeMcpService._iter_layers(layer.children)

    @staticmethod
    def _layer_bounds(layer: RenderLayerSnapshot) -> tuple[int, int, int, int] | None:
        image = layer.image
        if image is None or image.isNull():
            return None
        return (
            int(layer.offset_x),
            int(layer.offset_y),
            int(image.width()),
            int(image.height()),
        )

    def _dynamic_layer_assets(self) -> list[AssetRecord]:
        assets: list[AssetRecord] = []
        try:
            scene = self._current_scene_snapshot()
        except McpStructuredError:
            scene = None
        if scene is not None:
            page_id = self._active_page_id() or "current"
            for layer in scene.flat_layers:
                if layer.image is None or layer.image.isNull():
                    continue
                origin = to_jsonable(layer.origin)
                kind = (
                    AssetKind.GENERATED_OUTPUT.value
                    if isinstance(origin, Mapping)
                    and str(origin.get("type", "")).startswith("ai_effect")
                    else AssetKind.RASTER.value
                )
                assets.append(
                    AssetRecord(
                        asset_id=f"layer:{page_id}:{layer.id}",
                        kind=kind,
                        source="layer",
                        mime_type="image/png",
                        dimensions={
                            "width": int(layer.image.width()),
                            "height": int(layer.image.height()),
                        },
                        color_space="srgb",
                        related_layer_id=layer.id,
                        texture_version=int(layer.texture_version),
                        metadata={"pageId": page_id, "layerName": layer.name},
                    )
                )
        return assets

    def _find_render_layer(self, layer_id: str) -> RenderLayerSnapshot | None:
        try:
            scene = self._current_scene_snapshot()
        except McpStructuredError:
            return None
        for layer in scene.flat_layers:
            if layer.id == layer_id:
                return layer
        return None

    def _register_asset_payload(
        self,
        payload: bytes,
        *,
        kind: str,
        source: str,
        dimensions: dict[str, int],
        metadata: Mapping[str, Any] | None = None,
        related_layer_id: str | None = None,
        texture_version: int | None = None,
    ) -> str:
        asset_id = f"asset_{uuid.uuid4().hex}"
        normalized_dimensions = self._normalize_asset_dimensions(dimensions, payload)
        self._asset_payloads[asset_id] = payload
        self._asset_records[asset_id] = AssetRecord(
            asset_id=asset_id,
            kind=kind,
            source=source,
            mime_type="image/png",
            dimensions=normalized_dimensions,
            color_space="srgb",
            related_layer_id=related_layer_id,
            texture_version=texture_version,
            metadata=_safe_public_metadata(metadata),
        )
        return asset_id

    def _register_generated_asset(
        self,
        payload: bytes,
        *,
        source: str,
        dimensions: dict[str, int],
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        return self._register_asset_payload(
            payload,
            kind=AssetKind.GENERATED_OUTPUT.value,
            source=source,
            dimensions=dimensions,
            metadata=metadata,
        )

    @staticmethod
    def _normalize_asset_dimensions(
        dimensions: Mapping[str, Any] | None,
        payload: bytes | None,
    ) -> dict[str, int]:
        width = 0
        height = 0
        if isinstance(dimensions, Mapping):
            with contextlib.suppress(TypeError, ValueError):
                width = int(dimensions.get("width") or 0)
            with contextlib.suppress(TypeError, ValueError):
                height = int(dimensions.get("height") or 0)
        if (width <= 0 or height <= 0) and payload:
            image = QImage()
            if image.loadFromData(payload, "PNG") and not image.isNull():
                width = int(image.width())
                height = int(image.height())
        return {"width": max(0, width), "height": max(0, height)}

    @staticmethod
    def _validate_import_image_dimensions(width: int, height: int, file_name: str) -> None:
        if width <= 0 or height <= 0:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Input asset image has invalid dimensions.",
                details={"fileName": file_name, "width": width, "height": height},
                code=-32602,
            )
        if width > _MAX_IMPORT_IMAGE_DIMENSION or height > _MAX_IMPORT_IMAGE_DIMENSION:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Input asset image exceeds the MCP import dimension limit.",
                details={
                    "fileName": file_name,
                    "width": width,
                    "height": height,
                    "maxDimension": _MAX_IMPORT_IMAGE_DIMENSION,
                },
                code=-32602,
            )

    def _record_completed_job(
        self,
        job_class: str,
        *,
        result_asset_ids: Sequence[str],
        metadata: Mapping[str, Any],
    ) -> JobRecord:
        job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex}",
            job_class=job_class,
            status="completed",
            progress=1.0,
            result_asset_ids=list(result_asset_ids),
            metadata=scrub_secrets(dict(metadata)),
        )
        self._jobs[job.job_id] = job
        return job

    def _require_sam3_provider_asset_store(self) -> Any:
        if self._sam3_provider is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider is not available.",
                details={"mode": self.mode},
            )
        asset_store = getattr(self._sam3_provider, "asset_store", None)
        if asset_store is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "SAM3 provider asset store is not available.",
                details={"mode": self.mode},
            )
        return asset_store

    def _require_canvas_command_submitter(self) -> Any:
        if self.canvas_service is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas service is not available.",
                details={"mode": self.mode},
            )
        if not callable(getattr(self.canvas_service, "submit", None)):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas service command submit is not available.",
                details={"mode": self.mode},
            )
        return self.canvas_service

    @staticmethod
    def _required_str(arguments: Mapping[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                f"'{key}' is required.",
                details={"argument": key},
                code=-32602,
            )
        return value.strip()

    @staticmethod
    def _not_found(kind: str, identifier: str) -> McpStructuredError:
        return McpStructuredError(
            ReasonCode.NOT_FOUND,
            f"{kind} not found.",
            details={f"{kind}Id": identifier},
            code=-32602,
        )

    def _sam3_current_selection_context(self, source_ref: Any) -> Any:
        return self._sam3_selection_context_from_scene(
            self._current_scene_snapshot(),
            source_ref,
        )

    def _sam3_selection_context_from_scene(
        self,
        scene: RenderSceneSnapshot,
        source_ref: Any,
    ) -> Any:
        from loomstroke.ai_selection import Sam3SelectionContext  # noqa: PLC0415

        document = self._live_document_record()
        document_id = document.document_id if document is not None else "current"
        page_id = self._active_page_id()
        active_layer_id = str(scene.active_layer_id or "").strip() or None
        return Sam3SelectionContext(
            snapshot_id=str(getattr(source_ref, "snapshot_id", "") or ""),
            scope=str(getattr(source_ref, "kind", "") or "visible_composite"),
            canvas_width=int(scene.width),
            canvas_height=int(scene.height),
            document_id=document_id,
            page_id=page_id,
            layer_id=active_layer_id,
            dpi=_sam3_dpi_from_scene(scene),
            available_layer_ids=_sam3_scene_layer_ids(scene),
        )


def _sam3_surface_from_provider(provider: Any) -> Any | None:
    if provider is None:
        return None
    asset_store = getattr(provider, "asset_store", None)
    if asset_store is None:
        return None
    from loomstroke.ai_selection import Sam3ReadOnlySurface  # noqa: PLC0415

    return Sam3ReadOnlySurface(asset_store)


def _sam3_prompt_from_arguments(arguments: Mapping[str, Any]) -> Any:
    from .sam3_prompt_arguments import parse_sam3_prompt_arguments  # noqa: PLC0415

    return parse_sam3_prompt_arguments(arguments)


def _sam3_mapping_argument(arguments: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = arguments.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _sam3_invalid_argument(key, f"'{key}' must be an object.")
    from loomstroke.ai_selection.metadata_scrub import scrub_sam3_metadata  # noqa: PLC0415

    scrubbed = scrub_sam3_metadata(scrub_secrets(dict(value)))
    if not isinstance(scrubbed, Mapping):
        return {}
    return dict(scrubbed)


def _sam3_string_sequence_argument(value: Any, argument: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise _sam3_invalid_argument(argument, f"'{argument}' must be an array of strings.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _sam3_invalid_argument(argument, f"'{argument}' must be an array of strings.")
        text = item.strip()
        if text:
            items.append(text)
    return tuple(items)


def _sam3_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, Iterable) or isinstance(value, bytes | bytearray | Mapping):
        return []
    return [text for item in value if (text := str(item or "").strip())]


def _sam3_export_mask_package_candidates(
    candidate_set: Any,
    candidate_ids: Sequence[str] | None,
) -> tuple[Any, ...]:
    candidates = tuple(getattr(candidate_set, "candidates", ()) or ())
    if candidate_ids is None:
        return candidates
    wanted = set(candidate_ids)
    return tuple(
        candidate
        for candidate in candidates
        if str(getattr(candidate, "candidate_id", "") or "").strip() in wanted
    )


def _sam3_export_mask_package_output_block_code(
    service: LoomStrokeMcpService,
    output_directory: str,
) -> str | None:
    if not service.safety.roots:
        return "comic_mask_export_roots_required"
    try:
        service.safety.validate_output_path(
            Path(output_directory) / "__loomstroke_sam3_export_preflight__.tmp",
            overwrite=True,
        )
    except McpStructuredError as exc:
        if exc.details.get("policy") == "roots_required":
            return "comic_mask_export_roots_required"
        return "comic_mask_export_output_root_outside_allowed_roots"
    return None


def _sam3_export_mask_package_asset_refs(
    asset_store: Any,
    candidates: Sequence[Any],
    *,
    include_previews: bool,
) -> tuple[Any, ...]:
    from loomstroke.ai_selection import AssetRef  # noqa: PLC0415

    refs: list[AssetRef] = []
    seen_asset_ids: set[str] = set()
    for candidate in candidates:
        mask_asset_id = str(getattr(candidate, "mask_asset_id", "") or "").strip()
        if not mask_asset_id:
            raise _Sam3McpMaskPackageBlocked("comic_mask_asset_payload_asset_id_missing")
        if mask_asset_id not in seen_asset_ids:
            refs.append(_sam3_mask_asset_ref(asset_store, mask_asset_id))
            seen_asset_ids.add(mask_asset_id)

        if not include_previews:
            continue
        preview_asset_id = str(getattr(candidate, "preview_asset_id", "") or "").strip()
        if not preview_asset_id or preview_asset_id in seen_asset_ids:
            continue
        preview_ref = _sam3_optional_preview_asset_ref(asset_store, preview_asset_id)
        if preview_ref is not None:
            refs.append(preview_ref)
            seen_asset_ids.add(preview_asset_id)
    if not refs:
        raise _Sam3McpMaskPackageBlocked("comic_mask_export_asset_payload_missing")
    return tuple(refs)


def _sam3_mask_asset_ref(asset_store: Any, mask_asset_id: str) -> Any:
    from loomstroke.ai_selection import AssetRef  # noqa: PLC0415

    try:
        mask_asset = asset_store.load_mask_asset(mask_asset_id)
        mask_path = asset_store.mask_asset_path(mask_asset)
    except (OSError, ValueError) as exc:
        raise _Sam3McpMaskPackageBlocked("comic_mask_asset_payload_read_failed") from exc
    if not mask_path.is_file():
        raise _Sam3McpMaskPackageBlocked("comic_mask_asset_payload_read_failed")
    return AssetRef(
        asset_id=mask_asset.asset_id,
        uri=_sam3_export_asset_uri(mask_asset.asset_id, suffix=""),
        kind=str(mask_asset.kind),
        width=int(mask_asset.width),
        height=int(mask_asset.height),
        content_hash=str(mask_asset.content_hash),
        bbox=tuple(int(value) for value in mask_asset.bbox),
        metadata={},
    )


def _sam3_optional_preview_asset_ref(asset_store: Any, preview_asset_id: str) -> Any | None:
    from loomstroke.ai_selection import AssetRef  # noqa: PLC0415

    try:
        preview_asset = asset_store.load_preview_asset(preview_asset_id)
        preview_path = asset_store.preview_asset_path(preview_asset)
    except (OSError, ValueError):
        return None
    if not preview_path.is_file():
        return None
    return AssetRef(
        asset_id=preview_asset.asset_id,
        uri=_sam3_export_asset_uri(preview_asset.asset_id, suffix="_preview"),
        kind="preview_png",
        width=int(preview_asset.width),
        height=int(preview_asset.height),
        content_hash=str(preview_asset.content_hash),
        bbox=tuple(int(value) for value in preview_asset.bbox),
        metadata={},
    )


def _sam3_export_asset_uri(asset_id: str, *, suffix: str) -> str:
    text = str(asset_id or "").strip()
    stem = _sam3_safe_id(text, fallback="asset")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"assets/{stem}{suffix}-{digest}.png"


def _sam3_export_mask_package_confirmation_details(
    *,
    candidate_set_id: str,
    package_id: str,
    output_directory: str,
    candidate_count: int,
    asset_count: int,
    request_id: str,
) -> dict[str, Any]:
    return {
        "requestId": request_id or None,
        "candidateSetId": candidate_set_id,
        "packageId": package_id,
        "outputDirectory": output_directory,
        "candidateCount": max(0, int(candidate_count)),
        "assetCount": max(0, int(asset_count)),
    }


def _sam3_request_id(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"mcp-sam3-segment-{uuid.uuid4().hex}"


def _sam3_optional_str(value: Any, *, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _sam3_selection_apply_operation(value: Any) -> str:
    if value is None:
        return "replace"
    if not isinstance(value, str):
        raise _sam3_invalid_argument("operation", "'operation' must be a string.")
    text = value.strip()
    if not text:
        return "replace"
    if text not in {"replace", "add", "subtract", "intersect"}:
        raise _sam3_invalid_argument(
            "operation",
            "'operation' must be replace, add, subtract, or intersect.",
        )
    return text


def _sam3_dpi_from_scene(scene: RenderSceneSnapshot) -> int | None:
    value = getattr(scene.canvas_state, "dpi", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sam3_scene_layer_ids(scene: RenderSceneSnapshot) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    stack = list(getattr(scene, "layers", ()) or ())
    stack.extend(getattr(scene, "flat_layers", ()) or ())
    while stack:
        layer = stack.pop(0)
        layer_id = str(getattr(layer, "id", "") or "").strip()
        if layer_id:
            seen.setdefault(layer_id, None)
        stack.extend(getattr(layer, "children", ()) or ())
    return tuple(seen)


def _sam3_apply_selection_confirmation_details(
    plan: Any,
    *,
    request_id: str,
    page_id: str,
) -> dict[str, Any]:
    return {
        "requestId": request_id or None,
        "candidateSetId": plan.candidate_set_id,
        "candidateId": plan.candidate_id,
        "maskAssetId": plan.mask_asset_id,
        "operation": plan.operation,
        "selectionBounds": (
            list(plan.selection_bounds) if plan.selection_bounds is not None else None
        ),
        "pageId": page_id or None,
    }


def _sam3_apply_layer_mask_confirmation_details(
    plan: Any,
    *,
    target_layer_id: str,
    request_id: str,
    page_id: str,
) -> dict[str, Any]:
    return {
        "requestId": request_id or None,
        "candidateSetId": plan.candidate_set_id,
        "candidateId": plan.candidate_id,
        "maskAssetId": plan.mask_asset_id,
        "targetLayerId": target_layer_id,
        "maskEnabled": True,
        "selectionBounds": (
            list(plan.selection_bounds) if plan.selection_bounds is not None else None
        ),
        "pageId": page_id or None,
    }


def _sam3_segment_success_result(
    *,
    request_id: str,
    source_snapshot_id: str,
    candidate_set: Any,
) -> dict[str, Any]:
    candidate_resource_uris = [
        _sam3_resource_uri("candidate", candidate.candidate_id)
        for candidate in candidate_set.candidates
    ]
    mask_asset_resource_uris: list[str] = []
    seen_mask_asset_ids: set[str] = set()
    for candidate in candidate_set.candidates:
        if candidate.mask_asset_id in seen_mask_asset_ids:
            continue
        seen_mask_asset_ids.add(candidate.mask_asset_id)
        mask_asset_resource_uris.append(_sam3_resource_uri("mask-asset", candidate.mask_asset_id))
    return {
        "schemaVersion": _SAM3_SEGMENT_RESULT_SCHEMA_VERSION,
        "status": "completed",
        "blocked": False,
        "requestId": request_id,
        "sourceSnapshotId": source_snapshot_id,
        "candidateSetId": candidate_set.set_id,
        "jobId": candidate_set.job_id,
        "candidateCount": len(candidate_set.candidates),
        "candidateSetResourceUri": _sam3_resource_uri("candidate-set", candidate_set.set_id),
        "jobStatusResourceUri": _sam3_resource_uri("job", candidate_set.job_id),
        "candidateResourceUris": candidate_resource_uris,
        "maskAssetResourceUris": mask_asset_resource_uris,
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }


def _sam3_segment_blocked_result(
    *,
    asset_store: Any,
    request_id: str,
    source_snapshot_id: str,
    readiness: Any,
) -> dict[str, Any]:
    status = _sam3_provider_blocked_status(asset_store, request_id)
    result: dict[str, Any] = {
        "schemaVersion": _SAM3_SEGMENT_RESULT_SCHEMA_VERSION,
        "status": "blocked",
        "blocked": True,
        "requestId": request_id,
        "sourceSnapshotId": source_snapshot_id,
        "candidateCount": 0,
        "candidateResourceUris": [],
        "maskAssetResourceUris": [],
        "blockedReasons": _sam3_blocked_reasons(status=status, readiness=readiness),
        "recommendedActions": _sam3_recommended_actions(status=status, readiness=readiness),
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }
    if status is not None:
        result["jobId"] = status.job_id
        result["jobStatusResourceUri"] = _sam3_resource_uri("job", status.job_id)
    return result


def _sam3_segment_current_result(
    segment_result: Mapping[str, Any], source_ref: Any
) -> dict[str, Any]:
    result = dict(segment_result)
    result["schemaVersion"] = _SAM3_SEGMENT_CURRENT_RESULT_SCHEMA_VERSION
    result["sourceSnapshotId"] = source_ref.snapshot_id
    result["sourceSnapshotResourceUri"] = _sam3_resource_uri(
        "source-snapshot",
        source_ref.snapshot_id,
    )
    result["credentialValuesIncluded"] = False
    result["credentialReadPerformed"] = False
    return result


def _sam3_apply_layer_mask_result(
    plan: Any,
    *,
    target_layer_id: str,
    status: str,
    submitted: bool,
    blocked_reasons: Iterable[Any],
    warnings: Iterable[Any],
) -> dict[str, Any]:
    applied = status == "applied"
    result: dict[str, Any] = {
        "schemaVersion": _SAM3_APPLY_LAYER_MASK_RESULT_SCHEMA_VERSION,
        "status": status,
        "applied": applied,
        "blocked": not applied,
        "submitted": bool(submitted),
        "candidateSetId": plan.candidate_set_id,
        "candidateId": plan.candidate_id,
        "targetLayerId": target_layer_id,
        "maskAssetId": plan.mask_asset_id,
        "maskEnabled": True,
        "candidateSetResourceUri": _sam3_resource_uri("candidate-set", plan.candidate_set_id),
        "candidateResourceUri": _sam3_resource_uri("candidate", plan.candidate_id),
        "blockedReasons": _sam3_reason_payloads(blocked_reasons),
        "warnings": _sam3_reason_payloads(warnings),
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }
    if plan.mask_asset_id:
        result["maskAssetResourceUri"] = _sam3_resource_uri("mask-asset", plan.mask_asset_id)
    return result


def _sam3_export_mask_package_success_result(
    *,
    candidate_set_id: str,
    package_id: str,
    candidate_count: int,
    export_result: Mapping[str, Any],
) -> dict[str, Any]:
    export = export_result.get("export") if isinstance(export_result.get("export"), Mapping) else {}
    return {
        "schemaVersion": _SAM3_EXPORT_MASK_PACKAGE_RESULT_SCHEMA_VERSION,
        "status": "exported",
        "exported": True,
        "blocked": False,
        "candidateSetId": candidate_set_id,
        "packageId": package_id,
        "candidateCount": max(0, int(candidate_count)),
        "assetPayloadCount": _safe_int(export_result.get("assetPayloadCount"), default=0) or 0,
        "fileCount": _safe_int(_mapping_get(export, "fileCount"), default=0) or 0,
        "assetCount": _safe_int(_mapping_get(export, "assetCount"), default=0) or 0,
        "metadataPath": _str_or_none(_mapping_get(export, "metadataPath")),
        "candidateSetResourceUri": _sam3_resource_uri("candidate-set", candidate_set_id),
        "blockedReasonCodes": _sam3_string_list(export_result.get("blockedReasonCodes")),
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }


def _sam3_export_mask_package_blocked_result(
    *,
    candidate_set_id: str,
    blocked_reason_codes: Iterable[str],
    package_id: str | None = None,
    candidate_count: int = 0,
    metadata_path: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _SAM3_EXPORT_MASK_PACKAGE_RESULT_SCHEMA_VERSION,
        "status": "blocked",
        "exported": False,
        "blocked": True,
        "candidateSetId": candidate_set_id,
        "packageId": package_id,
        "candidateCount": max(0, int(candidate_count)),
        "assetPayloadCount": 0,
        "fileCount": 0,
        "assetCount": 0,
        "metadataPath": metadata_path,
        "candidateSetResourceUri": _sam3_resource_uri("candidate-set", candidate_set_id),
        "blockedReasonCodes": _sam3_string_list(blocked_reason_codes),
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }


def _sam3_apply_selection_result(execution: Any) -> dict[str, Any]:
    payload = execution.to_dict()
    plan = execution.plan
    result: dict[str, Any] = {
        "schemaVersion": _SAM3_APPLY_SELECTION_RESULT_SCHEMA_VERSION,
        "status": payload.get("status"),
        "applied": bool(payload.get("applied")),
        "blocked": not bool(payload.get("applied")),
        "submitted": bool(payload.get("submitted")),
        "candidateSetId": plan.candidate_set_id,
        "candidateId": plan.candidate_id,
        "maskAssetId": plan.mask_asset_id,
        "operation": plan.operation,
        "selectionBounds": (
            list(plan.selection_bounds) if plan.selection_bounds is not None else None
        ),
        "candidateSetResourceUri": _sam3_resource_uri("candidate-set", plan.candidate_set_id),
        "candidateResourceUri": _sam3_resource_uri("candidate", plan.candidate_id),
        "execution": payload,
        "blockedReasons": payload.get("blockedReasons", []),
        "warnings": payload.get("warnings", []),
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }
    if plan.mask_asset_id:
        result["maskAssetResourceUri"] = _sam3_resource_uri("mask-asset", plan.mask_asset_id)
    return result


def _sam3_reason_payloads(items: Iterable[Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in items:
        to_dict = getattr(item, "to_dict", None)
        if callable(to_dict):
            payload = to_dict()
        elif isinstance(item, Mapping):
            payload = dict(item)
        else:
            payload = {"code": str(item)}
        payloads.append(cast(dict[str, Any], to_jsonable(scrub_secrets(payload))))
    return payloads


def _sam3_overlay_preview_success_result(
    *,
    candidate_set_id: str,
    preview_asset: Any,
) -> dict[str, Any]:
    return {
        "schemaVersion": _SAM3_OVERLAY_PREVIEW_RESULT_SCHEMA_VERSION,
        "status": "completed",
        "blocked": False,
        "candidateSetId": candidate_set_id,
        "previewAssetId": preview_asset.asset_id,
        "previewAssetResourceUri": _sam3_resource_uri("preview-asset", preview_asset.asset_id),
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }


def _sam3_cancel_success_result(status: Any) -> dict[str, Any]:
    return {
        "schemaVersion": _SAM3_CANCEL_RESULT_SCHEMA_VERSION,
        "status": "cancelled",
        "cancelled": True,
        "blocked": False,
        "jobId": status.job_id,
        "jobStatusResourceUri": _sam3_resource_uri("job", status.job_id),
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }


def _sam3_cancel_blocked_result(status: Any) -> dict[str, Any]:
    return {
        "schemaVersion": _SAM3_CANCEL_RESULT_SCHEMA_VERSION,
        "status": "not_cancelled",
        "cancelled": False,
        "blocked": True,
        "jobId": status.job_id,
        "jobStatusResourceUri": _sam3_resource_uri("job", status.job_id),
        "blockedReasons": [
            {
                "code": "sam3_job_not_cancellable",
                "message": "SAM3 job could not be cancelled.",
                "action": "Read the job status and retry only if the worker still owns the job.",
            }
        ],
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }


def _sam3_provider_blocked_status(asset_store: Any, request_id: str) -> Any | None:
    expected_job_id = f"sam3-provider-segment-{_sam3_safe_id(request_id, fallback='segment')}"
    try:
        return asset_store.load_job_status(expected_job_id)
    except (OSError, ValueError):
        pass
    try:
        statuses = asset_store.list_job_statuses()
    except (OSError, ValueError):
        return None
    matches = [
        status
        for status in statuses
        if str(getattr(status, "job_id", "")).startswith("sam3-provider-segment-")
        and getattr(status, "status", None) == "blocked"
        and getattr(status, "diagnostics", {}).get("operation") == "segment"
    ]
    return sorted(matches, key=lambda item: item.job_id)[-1] if matches else None


def _sam3_blocked_reasons(*, status: Any | None, readiness: Any) -> list[dict[str, Any]]:
    if status is not None and getattr(status, "blocked_reasons", ()):
        return [reason.to_dict() for reason in status.blocked_reasons]
    if readiness is not None and getattr(readiness, "blocked_reasons", ()):
        return [reason.to_dict() for reason in readiness.blocked_reasons]
    return [
        {
            "code": "sam3_worker_unavailable",
            "message": "SAM3 worker is unavailable.",
            "action": "Check SAM3 readiness before running segmentation again.",
        }
    ]


def _sam3_recommended_actions(*, status: Any | None, readiness: Any) -> list[str]:
    if status is not None and getattr(status, "recommended_actions", ()):
        return list(status.recommended_actions)
    if readiness is not None and getattr(readiness, "recommended_actions", ()):
        return list(readiness.recommended_actions)
    return ["Check SAM3 readiness before running segmentation again."]


def _sam3_overlay_color_mode(value: Any) -> str:
    text = str(value or "instance").strip().lower()
    return text if text in {"instance", "single", "heat"} else "instance"


class _Sam3McpMaskPackageBlocked(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _sam3_safe_id(value: str, *, fallback: str) -> str:
    normalized = _SAM3_SAFE_ID_PATTERN.sub("-", str(value).strip()).strip(".-_")
    if not normalized:
        normalized = fallback
    return normalized[:64]


def _sam3_resource_uri(kind: str, identifier: str) -> str:
    return f"loomstroke://sam3/{kind}/{quote(str(identifier), safe='')}"


def _sam3_invalid_argument(argument: str, message: str) -> McpStructuredError:
    return McpStructuredError(
        ReasonCode.INVALID_ARGUMENT,
        message,
        details={"argument": argument},
        code=-32602,
    )


def _generation_submission_summary(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return confirmation-safe generation request details."""

    metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), Mapping) else {}
    param_values = (
        arguments.get("paramValues") if isinstance(arguments.get("paramValues"), Mapping) else {}
    )
    summary = {
        "layerId": str(arguments.get("layerId") or ""),
        "applyToCanvas": bool(arguments.get("applyToCanvas", True)),
        "candidateRequest": bool(_mapping_get(metadata, "candidateRequest")),
        "candidateCount": _safe_int(_mapping_get(metadata, "candidateCount"), default=None),
        "source": _str_or_none(_mapping_get(metadata, "source")),
        "sourcePromptDigest": _str_or_none(_mapping_get(metadata, "sourcePromptDigest")),
        "presetId": _str_or_none(arguments.get("presetId") or _mapping_get(metadata, "presetId")),
        "gatewayId": _str_or_none(
            arguments.get("gatewayId") or _mapping_get(metadata, "gatewayId")
        ),
        "paramKeys": sorted(str(key) for key in param_values),
        "payloadOmitted": True,
    }
    return scrub_secrets({key: value for key, value in summary.items() if value is not None})


def _job_submission_metadata(job: JobRecord) -> dict[str, Any]:
    submission = job.metadata.get("submission")
    return dict(submission) if isinstance(submission, Mapping) else {}


def _generation_asset_public_metadata(asset: Mapping[str, Any]) -> dict[str, Any]:
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), Mapping) else {}
    return _safe_public_metadata(metadata)


def _safe_public_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    safe, _ = _omit_generation_payloads(scrub_secrets(dict(metadata or {})))
    return cast(dict[str, Any], safe)


def _omit_generation_payloads(value: Any) -> tuple[Any, bool]:
    """Recursively omit raw prompt/request/binary payload fields."""

    if isinstance(value, Mapping):
        omitted = False
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if _normalized_metadata_key(key) in _GENERATION_PAYLOAD_METADATA_KEYS:
                omitted = True
                continue
            cleaned_item, child_omitted = _omit_generation_payloads(item)
            cleaned[str(key)] = cleaned_item
            omitted = omitted or child_omitted
        return cleaned, omitted
    if isinstance(value, list):
        omitted = False
        cleaned_items: list[Any] = []
        for item in value:
            cleaned_item, child_omitted = _omit_generation_payloads(item)
            cleaned_items.append(cleaned_item)
            omitted = omitted or child_omitted
        return cleaned_items, omitted
    if isinstance(value, tuple):
        cleaned_items, omitted = _omit_generation_payloads(list(value))
        return cleaned_items, omitted
    return value, False


def _normalized_metadata_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(ch for ch in text if ch.isalnum() or ch == "_")


def _mapping_get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else None


def _safe_int(value: Any, *, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _array_to_qimage(data: np.ndarray) -> QImage:
    return _shared_array_to_qimage(data)


def _flatten_render_layers(
    layers: Sequence[RenderLayerSnapshot],
    parent_id: str | None = None,
) -> tuple[list[RenderLayerSnapshot], list[str | None]]:
    return _shared_flatten_render_layers(layers, parent_id)


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _qimage_reader_format_name(reader: QImageReader) -> str | None:
    try:
        raw = bytes(reader.format())
    except Exception:
        raw = b""
    text = raw.decode("ascii", errors="ignore").strip().lower()
    return text or None


def _list_values(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _first_nonempty_str(arguments: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _comic_workflow_plan_args(arguments: Mapping[str, Any], script: str) -> dict[str, Any]:
    plan_args: dict[str, Any] = {"script": script}
    for key in ("page", "style", "assetIds", "constraints"):
        value = arguments.get(key)
        if value is not None:
            plan_args[key] = value
    export_options = arguments.get("export")
    if isinstance(export_options, Mapping):
        plan_args["export"] = dict(export_options)
    return plan_args


def _comic_workflow_preview_args(
    arguments: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    preview_args: dict[str, Any] = {
        "plan": plan,
        "registerAsset": bool(arguments.get("registerAsset", True)),
    }
    for optional_key in ("baseAssetId", "outputPath", "overwrite"):
        if optional_key in arguments:
            preview_args[optional_key] = arguments[optional_key]
    return preview_args


def _comic_workflow_export_request(arguments: Mapping[str, Any]) -> tuple[bool, Any]:
    requested_export = bool(arguments.get("exportPackage", False))
    output_directory = arguments.get("outputDirectory")
    export_options = arguments.get("export")
    if isinstance(export_options, Mapping) and export_options.get("outputDirectory"):
        output_directory = export_options.get("outputDirectory")
        requested_export = True
    if isinstance(output_directory, str) and output_directory.strip():
        requested_export = True
    return requested_export, output_directory


def _comic_workflow_export_args(
    arguments: Mapping[str, Any],
    plan: Mapping[str, Any],
    output_directory: str,
) -> dict[str, Any]:
    export_args: dict[str, Any] = {
        "plan": plan,
        "outputDirectory": output_directory,
        "overwrite": bool(arguments.get("overwrite", False)),
        "registerAsset": bool(arguments.get("registerAsset", True)),
    }
    for optional_key in ("fileName", "baseAssetId"):
        if optional_key in arguments:
            export_args[optional_key] = arguments[optional_key]
    return export_args


def _detect_create_intent(arguments: Mapping[str, Any], request: str) -> str:
    domain = str(arguments.get("domain") or arguments.get("kind") or "").strip().lower()
    text = unicodedata.normalize("NFKC", f"{domain} {request}").casefold()
    if any(token in text for token in ("漫画", "マンガ", "comic", "manga", "4コマ", "yonkoma")):
        return "comic_page"
    if any(token in text for token in ("3d", "mesh", "model", "game asset", "ゲーム素材")):
        return "game_asset_3d"
    if any(token in text for token in ("character sheet", "キャラシート", "設定資料")):
        return "character_sheet"
    if any(token in text for token in ("character", "キャラクター", "キャラデザ")):
        return "character_design"
    return "graphic_plan"


def _generic_create_comic_args(arguments: Mapping[str, Any], request: str) -> dict[str, Any]:
    comic_args: dict[str, Any] = {
        "prompt": request,
        "registerAsset": bool(arguments.get("registerAsset", True)),
    }
    for key in (
        "page",
        "style",
        "assetIds",
        "constraints",
        "export",
        "outputPath",
        "outputDirectory",
        "fileName",
        "overwrite",
        "baseAssetId",
        "exportPackage",
    ):
        if key in arguments:
            comic_args[key] = arguments[key]
    return comic_args


def _comic_workflow_script(arguments: Mapping[str, Any]) -> str:
    for key in ("script", "story", "prompt", "request", "instructions"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise McpStructuredError(
        ReasonCode.INVALID_ARGUMENT,
        "'script' is required.",
        details={
            "argument": "script",
            "acceptedAliases": ["story", "prompt", "request", "instructions"],
        },
        code=-32602,
    )


def _generic_create_production_input(
    arguments: Mapping[str, Any],
    request: str,
) -> dict[str, Any]:
    supplied = arguments.get("input")
    payload = dict(supplied) if isinstance(supplied, Mapping) else {}
    payload.setdefault("prompt", request)
    for key in ("target", "constraints", "style", "qualityProfileId", "candidatePolicy"):
        if key in arguments and key not in payload:
            payload[key] = arguments[key]
    return payload


def _recipe_id_for_create_intent(intent: str) -> str | None:
    return {
        "character_design": "character.design.create",
        "character_sheet": "character.sheet.create",
        "game_asset_3d": "game_asset_3d.create",
    }.get(intent)


def _generic_create_response(
    *,
    intent: str,
    routed_tool: str,
    result: Mapping[str, Any],
    completed_steps: Sequence[str],
    blocked_reasons: Sequence[Mapping[str, Any]] = (),
    next_actions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    nested_reasons = _collect_blocked_reasons(result)
    own_reasons = [dict(item) for item in blocked_reasons]
    reasons = _collect_blocked_reasons(
        {"blockedReasons": [*own_reasons, *nested_reasons]},
    )
    nested_actions = _collect_next_actions(result)
    actions = _collect_next_actions({"nextActions": [*next_actions, *nested_actions]})
    return {
        "schemaVersion": "mcp.create.v1",
        "ok": True,
        "intent": intent,
        "routedTool": routed_tool,
        "completedSteps": list(completed_steps),
        "blocked": bool(reasons),
        "blockedReasons": reasons,
        "result": dict(result),
        "nextActions": actions,
    }


def _blocked_file_write_next_actions() -> list[dict[str, str]]:
    return [
        {
            "kind": "use_preview_asset",
            "description": "Use the returned MCP assetId as the completed preview output.",
        },
        {
            "kind": "retry_live_endpoint",
            "description": (
                "Use the live LoomStroke HTTP MCP endpoint when a rooted file export is required."
            ),
        },
    ]


def _collect_next_actions(*payloads: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        for action in payload.get("nextActions", []):
            if not isinstance(action, Mapping):
                continue
            key = json.dumps(action, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            actions.append(dict(action))
    return actions


def _collect_blocked_reasons(*payloads: Mapping[str, Any]) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        for reason in payload.get("blockedReasons", []):
            if not isinstance(reason, Mapping):
                continue
            key = json.dumps(reason, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            reasons.append(dict(reason))
    return reasons


def _blocked_comic_export_response(
    *,
    code: str,
    message: str,
    suggested_action: str,
) -> dict[str, Any]:
    reason = {"code": code, "message": message, "action": suggested_action}
    return {
        "schemaVersion": "mcp.comic_package.v1",
        "ok": True,
        "blocked": True,
        "blockedReasons": [reason],
        "packageDirectory": None,
        "registered": False,
        "files": [],
        "warnings": [code],
        "nextActions": [
            {
                "kind": suggested_action,
                "description": message,
            }
        ],
    }


def _comic_package_file_name(value: Any, *, suffix: str) -> str:
    name = str(value or "").strip()
    if not name:
        name = f"comic_preview{suffix}"
    candidate = Path(name)
    if (
        candidate.name != name
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or "\0" in name
    ):
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "Comic package fileName must be a plain file name.",
            details={"fileName": name},
            code=-32602,
        )
    if candidate.suffix.lower() != suffix:
        name = f"{name}{suffix}"
    return name


def _json_payload_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _json_payload_size(payload: Any) -> int:
    return len(_json_payload_bytes(payload))


def _write_bytes_atomically(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_bytes(payload)
        tmp.replace(target)
    except Exception as exc:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED,
            "Comic package export failed.",
            details={"error": exc.__class__.__name__, "path": str(target)},
            code=-32603,
        ) from exc


def _write_json_atomically(target: Path, payload: Any) -> None:
    _write_bytes_atomically(target, _json_payload_bytes(payload))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _overlay_rect(overlay: Mapping[str, Any]) -> QRectF:
    rect = overlay.get("rect")
    if not isinstance(rect, Mapping):
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'rect' must be an object.",
            details={"argument": "rect"},
            code=-32602,
        )
    x = _bounded_float(rect.get("x"), default=0.0, minimum=-1_000_000.0, maximum=1_000_000.0)
    y = _bounded_float(rect.get("y"), default=0.0, minimum=-1_000_000.0, maximum=1_000_000.0)
    width = _bounded_float(
        rect.get("width"),
        default=0.0,
        minimum=0.0,
        maximum=1_000_000.0,
    )
    height = _bounded_float(
        rect.get("height"),
        default=0.0,
        minimum=0.0,
        maximum=1_000_000.0,
    )
    if width <= 0 or height <= 0:
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'rect.width' and 'rect.height' must be positive.",
            details={"argument": "rect"},
            code=-32602,
        )
    return QRectF(x, y, width, height)


def _qcolor(value: Any, default: QColor) -> QColor:
    if isinstance(value, str) and value.strip():
        color = QColor(value.strip())
        if color.isValid():
            return color
    return QColor(default)


def _normalized_or_pixel(
    value: Any,
    *,
    base: int,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    parsed = _bounded_float(
        value,
        default=default,
        minimum=-1_000_000.0,
        maximum=1_000_000.0,
    )
    pixels = parsed * base if 0.0 <= parsed <= 1.0 else parsed
    return max(minimum, min(maximum, pixels))


def _bubble_anchor_coordinate(
    anchor: Any,
    key: str,
    base: int,
    *,
    default: float,
) -> float:
    value = anchor.get(key) if isinstance(anchor, Mapping) else None
    return _normalized_or_pixel(
        value,
        base=base,
        default=default,
        minimum=0.0,
        maximum=float(base),
    )


def _normalize_tail_direction(value: Any) -> str:
    direction = str(value or "none").strip().lower()
    if direction in {"left", "right", "up", "down", "none"}:
        return direction
    return "none"


def _bubble_body_rect(
    anchor_x: float,
    anchor_y: float,
    width: float,
    height: float,
    *,
    image_width: float,
    image_height: float,
    tail_direction: str,
    tail_length: float,
) -> QRectF:
    x = anchor_x - width / 2.0
    y = anchor_y - height / 2.0
    min_x = tail_length if tail_direction == "left" else 0.0
    max_x = image_width - width - (tail_length if tail_direction == "right" else 0.0)
    min_y = tail_length if tail_direction == "up" else 0.0
    max_y = image_height - height - (tail_length if tail_direction == "down" else 0.0)
    x = max(min_x, min(max_x, x)) if max_x >= min_x else max(0.0, (image_width - width) / 2.0)
    y = max(min_y, min(max_y, y)) if max_y >= min_y else max(0.0, (image_height - height) / 2.0)
    return QRectF(x, y, width, height)


def _bubble_radius(bubble: Mapping[str, Any], rect: QRectF) -> float:
    style = str(bubble.get("style") or "manga_round").strip().lower()
    default_radius = min(36.0, rect.height() / 2.0)
    if style in {"manga_rect", "rect", "caption"}:
        default_radius = min(8.0, rect.height() / 4.0)
    return _bounded_float(
        bubble.get("radius"),
        default=default_radius,
        minimum=0.0,
        maximum=200.0,
    )


def _bubble_tail_polygon(
    rect: QRectF,
    direction: str,
    length: float,
) -> QPolygonF | None:
    if direction == "none" or length <= 0:
        return None
    spread = min(28.0, max(10.0, min(rect.width(), rect.height()) * 0.18))
    cx = rect.center().x()
    cy = rect.center().y()
    if direction == "left":
        return QPolygonF(
            [
                QPointF(rect.left(), cy - spread),
                QPointF(rect.left() - length, cy),
                QPointF(rect.left(), cy + spread),
            ]
        )
    if direction == "right":
        return QPolygonF(
            [
                QPointF(rect.right(), cy - spread),
                QPointF(rect.right() + length, cy),
                QPointF(rect.right(), cy + spread),
            ]
        )
    if direction == "up":
        return QPolygonF(
            [
                QPointF(cx - spread, rect.top()),
                QPointF(cx, rect.top() - length),
                QPointF(cx + spread, rect.top()),
            ]
        )
    if direction == "down":
        return QPolygonF(
            [
                QPointF(cx - spread, rect.bottom()),
                QPointF(cx, rect.bottom() + length),
                QPointF(cx + spread, rect.bottom()),
            ]
        )
    return None


def _rect_schema(rect: QRectF) -> dict[str, float]:
    return {
        "x": round(float(rect.x()), 3),
        "y": round(float(rect.y()), 3),
        "width": round(float(rect.width()), 3),
        "height": round(float(rect.height()), 3),
    }


def _draw_overlay_rect(
    painter: QPainter,
    rect: QRectF,
    overlay: Mapping[str, Any],
    radius: float,
    stroke_width: float,
) -> None:
    fill = _qcolor(overlay.get("fillColor"), QColor(255, 255, 255, 0))
    stroke = _qcolor(overlay.get("strokeColor"), QColor(255, 255, 255, 230))
    painter.setBrush(QBrush(fill))
    if stroke_width <= 0:
        painter.setPen(Qt.PenStyle.NoPen)
    else:
        painter.setPen(QPen(stroke, stroke_width))
    painter.drawRoundedRect(rect, radius, radius)


def _draw_overlay_text_box(
    painter: QPainter,
    rect: QRectF,
    overlay: Mapping[str, Any],
    radius: float,
    stroke_width: float,
) -> dict[str, Any]:
    from .comic_text_layout import fit_text_box  # noqa: PLC0415

    background = overlay.get("background")
    transparent_background = bool(overlay.get("transparentTextBox")) or (
        isinstance(background, str) and background.strip().lower() == "transparent"
    )
    fill_source = overlay.get("fillColor")
    if fill_source is None and not transparent_background:
        fill_source = background
    fill = (
        QColor(255, 255, 255, 0)
        if transparent_background or fill_source is None
        else _qcolor(fill_source, QColor(255, 255, 255, 0))
    )
    stroke = _qcolor(overlay.get("strokeColor"), QColor(255, 255, 255, 0))
    text_color = _qcolor(overlay.get("textColor"), QColor(16, 16, 16, 255))
    font_size = _bounded_int(overlay.get("fontSize"), default=20, minimum=6, maximum=256)
    fit_mode = str(overlay.get("fitMode") or overlay.get("fit") or "shrink_to_fit")
    min_font_size = _bounded_int(
        overlay.get("minFontSize"),
        default=6,
        minimum=6,
        maximum=font_size,
    )
    text = str(overlay.get("text") or "")[:2000]
    default_inset = min(max(12.0, font_size * 0.45), rect.width() / 4.0, rect.height() / 4.0)
    inset = _bounded_float(
        overlay.get("safePadding", overlay.get("padding")),
        default=default_inset,
        minimum=0.0,
        maximum=max(0.0, min(rect.width(), rect.height()) / 2.0 - 1.0),
    )
    text_rect = rect.adjusted(inset, inset, -inset, -inset)
    fit = fit_text_box(
        text,
        width=text_rect.width(),
        height=text_rect.height(),
        font_size=font_size,
        fit_mode=fit_mode,
        min_font_size=min_font_size,
    )
    painter.setBrush(QBrush(fill))
    if stroke_width <= 0:
        painter.setPen(Qt.PenStyle.NoPen)
    else:
        painter.setPen(QPen(stroke, stroke_width))
    painter.drawRoundedRect(rect, radius, radius)
    if QGuiApplication.instance() is None:
        return fit.to_fit_report(
            text_id=str(overlay.get("id") or ""),
            role="overlay",
            panel_id="asset_overlay",
            include_visual_risks=True,
        )
    from .comic_preview import comic_text_font  # noqa: PLC0415

    painter.setFont(comic_text_font(fit.font_size))
    painter.setPen(QPen(text_color, 1))
    flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
    painter.drawText(text_rect, flags, text)
    return fit.to_fit_report(
        text_id=str(overlay.get("id") or ""),
        role="overlay",
        panel_id="asset_overlay",
        include_visual_risks=True,
    )
