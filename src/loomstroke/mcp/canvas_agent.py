"""Read-only Canvas Agent runtime for MCP and in-process callers."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from .canvas_candidate_stack import (
    build_canvas_candidate_stack,
    build_selected_candidate_changeset,
    selected_candidate,
    with_selected_candidate,
)
from .canvas_changeset import (
    build_canvas_changeset,
    build_canvas_changeset_preview,
    canvas_changeset_hash,
    validate_canvas_changeset,
)
from .canvas_changeset_apply import (
    CanvasChangesetApplyCommand,
    asset_ids_for_apply,
    build_apply_result,
    build_verification_result,
    unsupported_apply_operations,
)
from .canvas_planner import (
    CanvasAgentReadOnlyPlannerTools,
    read_only_tool_allowlist_hash,
    strip_planner_payloads,
    validate_canvas_edit_plan_candidate,
)
from .canvas_visual_preview import (
    CanvasAgentVisualPreviewer,
    build_visual_preview_metadata_fallback,
    strip_canvas_agent_payloads,
)
from .editing_revision import metadata_revision
from .generation_capabilities import generation_submitter_capabilities
from .safety import scrub_secrets
from .serialization import qcolor_to_rgba, qrect_to_tuple, to_jsonable
from .types import McpStructuredError, ReasonCode

CONTEXT_SCHEMA_VERSION = "canvas_edit_context.v1"
PLAN_SCHEMA_VERSION = "canvas_edit_plan.v1"
RUN_SCHEMA_VERSION = "canvas_agent_run.v1"
STYLE_MEMORY_SCHEMA_VERSION = "canvas_agent_style_memory.v1"
CLARIFICATION_SCHEMA_VERSION = "canvas_clarification.v1"
CLARIFICATION_ANSWER_RESULT_SCHEMA_VERSION = "canvas_clarification_answer_result.v1"

MAX_PROMPT_LENGTH = 8_000
MAX_CLIENT_REQUEST_ID_LENGTH = 128
DEFAULT_MAX_LAYER_COUNT = 80
MAX_LAYER_COUNT = 256
MAX_STYLE_MEMORY_ENTRIES = 12
ROLE_CONFIDENCE_THRESHOLD = 0.75


@dataclass(slots=True)
class AgentRunRecord:
    run_id: str
    state: str
    created_at: str
    updated_at: str
    context: dict[str, Any]
    plan: dict[str, Any] | None = None
    changeset: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    apply_result: dict[str, Any] | None = None
    log: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        document_mutation = (
            str(self.apply_result.get("documentMutation") or "command_backed")
            if self.apply_result is not None
            else "none"
        )
        payload = {
            "schemaVersion": RUN_SCHEMA_VERSION,
            "runId": self.run_id,
            "state": self.state,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "contextId": self.context.get("contextId"),
            "planId": (self.plan or {}).get("planId"),
            "readOnly": self.apply_result is None,
            "documentMutation": document_mutation,
        }
        if self.changeset is not None:
            payload["changesetId"] = self.changeset.get("changesetId")
        plan = self.plan if isinstance(self.plan, Mapping) else {}
        clarification = (
            plan.get("clarification") if isinstance(plan.get("clarification"), Mapping) else {}
        )
        if clarification.get("clarificationId"):
            payload["clarificationId"] = clarification.get("clarificationId")
        if isinstance(plan.get("resolvedClarification"), Mapping):
            payload["clarificationAnswer"] = True
        if self.preview is not None:
            payload["previewId"] = self.preview.get("previewId")
            visual_preview = self.preview.get("visualPreview")
            if isinstance(visual_preview, Mapping):
                payload["visualPreviewId"] = visual_preview.get("visualPreviewId")
        if self.apply_result is not None:
            payload["applyId"] = self.apply_result.get("applyId")
        return payload

    def to_schema(self) -> dict[str, Any]:
        payload = self.summary()
        payload["context"] = self.context
        payload["plan"] = self.plan
        if self.changeset is not None:
            payload["changeset"] = self.changeset
        if self.preview is not None:
            payload["preview"] = self.preview
        if self.apply_result is not None:
            payload["applyResult"] = self.apply_result
        payload["log"] = list(self.log)
        return scrub_secrets(payload)


class CanvasAgentToolRegistry:
    """Read-only tool allowlist for the Phase 0 canvas agent loop."""

    _TOOLS: ClassVar[dict[str, dict[str, Any]]] = {
        "loomstroke_get_edit_context": {"readOnly": True, "documentMutation": "none"},
        "loomstroke_plan_canvas_edit": {"readOnly": True, "documentMutation": "none"},
        "loomstroke_list_ai_effect_presets": {"readOnly": True, "documentMutation": "none"},
        "loomstroke_get_ai_effect_catalog": {"readOnly": True, "documentMutation": "none"},
        "loomstroke_render_preview": {"readOnly": True, "documentMutation": "none"},
        "loomstroke_graphic_plan": {"readOnly": True, "documentMutation": "none"},
        "loomstroke_create_canvas_changeset": {
            "readOnly": False,
            "documentMutation": "none",
        },
        "loomstroke_preview_canvas_changeset": {
            "readOnly": False,
            "documentMutation": "none",
        },
        "loomstroke_answer_canvas_clarification": {
            "readOnly": False,
            "documentMutation": "none",
        },
        "loomstroke_apply_canvas_changeset": {
            "readOnly": False,
            "documentMutation": "command_backed",
        },
        "loomstroke_verify_canvas_edit": {"readOnly": True, "documentMutation": "none"},
    }

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                **metadata,
            }
            for name, metadata in self._TOOLS.items()
        ]

    def ensure_allowed(self, name: str) -> None:
        if name not in self._TOOLS:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Canvas Agent only allows registered safe loop tools.",
                details={"tool": name, "allowedTools": list(self._TOOLS.keys())},
            )


class InProcessMcpToolAdapter:
    """Tiny in-process adapter mirroring the MCP tool boundary without HTTP."""

    def __init__(self, service: Any, registry: CanvasAgentToolRegistry) -> None:
        self._service = service
        self._registry = registry

    async def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        self._registry.ensure_allowed(name)
        args = dict(arguments or {})
        sync_handlers = {
            "loomstroke_get_edit_context": self._service.get_edit_context,
            "loomstroke_plan_canvas_edit": self._service.plan_canvas_edit,
            "loomstroke_graphic_plan": self._service.graphic_plan,
            "loomstroke_render_preview": self._service.render_preview,
            "loomstroke_create_canvas_changeset": self._service.create_canvas_changeset,
            "loomstroke_preview_canvas_changeset": self._service.preview_canvas_changeset,
            "loomstroke_answer_canvas_clarification": (self._service.answer_canvas_clarification),
            "loomstroke_verify_canvas_edit": self._service.verify_canvas_edit,
        }
        handler = sync_handlers.get(name)
        if handler is not None:
            return handler(args)
        if name == "loomstroke_apply_canvas_changeset":
            return await self._service.apply_canvas_changeset(args)
        if name == "loomstroke_list_ai_effect_presets":
            return await self._service.list_ai_effect_presets()
        if name == "loomstroke_get_ai_effect_catalog":
            return await self._service.get_ai_effect_catalog(args)
        raise McpStructuredError(
            ReasonCode.NOT_FOUND,
            "Canvas Agent tool was not found.",
            details={"tool": name},
        )


class CanvasAgentRuntime:
    """Phase 0 Canvas Agent runtime.

    The runtime is intentionally read-only: it builds a lightweight edit
    context and a deterministic local plan, and it never starts an external
    model, credential read, generation job, or document mutation.
    """

    def __init__(self, service: Any) -> None:
        self._service = service
        self.tool_registry = CanvasAgentToolRegistry()
        self.tool_adapter = InProcessMcpToolAdapter(service, self.tool_registry)
        self._runs: dict[str, AgentRunRecord] = {}
        self._changesets: dict[str, dict[str, Any]] = {}
        self._previews: dict[str, dict[str, Any]] = {}
        self._apply_results: dict[str, dict[str, Any]] = {}
        self._approval_tickets: dict[str, dict[str, Any]] = {}
        self._last_context: dict[str, Any] | None = None
        self._style_memory_entries: list[dict[str, Any]] = []
        self._last_planner_provider = "deterministic"
        self._planner_credential_read_performed = False

    def get_edit_context(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        max_layers = _bounded_int(
            args.get("maxLayerCount"),
            default=DEFAULT_MAX_LAYER_COUNT,
            minimum=1,
            maximum=MAX_LAYER_COUNT,
        )
        include_hidden = bool(args.get("includeHidden", True))
        include_thumbnail = bool(args.get("includeThumbnail", False))

        scene = self._service.get_scene_snapshot(raw=True)
        documents = _safe_list(self._service.list_documents)
        pages = _safe_list(self._service.list_pages)
        document = _first_live_or_first(documents)
        page = _active_or_first_page(pages, document)

        flat_layers = [
            layer for layer in scene.flat_layers if include_hidden or bool(layer.visible)
        ]
        total_layers = len(flat_layers)
        layers = [
            _layer_context(layer, index) for index, layer in enumerate(flat_layers[:max_layers])
        ]
        active_layer = next(
            (layer for layer in layers if layer.get("layerId") == scene.active_layer_id),
            None,
        )
        canvas = {
            "width": int(scene.canvas_state.width),
            "height": int(scene.canvas_state.height),
            "dpi": int(scene.canvas_state.dpi),
            "background": qcolor_to_rgba(scene.canvas_state.background),
            "activeLayerId": scene.active_layer_id,
            "activeFlatIndex": int(scene.active_flat_index),
            "layerCacheVersion": int(scene.canvas_state.layer_cache_version),
        }
        selection = _selection_context(scene)
        context_id = f"ctx_{uuid.uuid4().hex[:12]}"
        created_at = _now()
        context = {
            "schemaVersion": CONTEXT_SCHEMA_VERSION,
            "contextId": context_id,
            "createdAt": created_at,
            "document": {
                "documentId": _str_or_none(
                    document.get("documentId") or document.get("document_id")
                ),
                "name": _str_or_none(document.get("name")),
                "source": _str_or_none(document.get("source")),
                "activePageId": _str_or_none(
                    document.get("activePageId") or document.get("active_page_id")
                ),
            },
            "page": {
                "pageId": _str_or_none(page.get("pageId") or page.get("page_id")),
                "name": _str_or_none(page.get("name")),
                "index": page.get("index"),
                "dimensions": page.get("dimensions"),
            },
            "canvas": canvas,
            "layers": layers,
            "layerSummary": {
                "total": total_layers,
                "returned": len(layers),
                "truncated": total_layers > len(layers),
                "includeHidden": include_hidden,
            },
            "activeLayer": active_layer,
            "selection": selection,
            "vector": _vector_context(scene),
            "history": _history_context(getattr(self._service, "canvas_service", None)),
            "styleMemory": _style_memory_context(self._style_memory_entries),
            "thumbnail": {
                "requested": include_thumbnail,
                "included": False,
                "reason": (
                    "thumbnail_asset_not_created_in_phase0"
                    if include_thumbnail
                    else "not_requested"
                ),
            },
            "aiReadiness": _ai_readiness(self._service),
            "safety": _safety_context(self._service),
            "contextDigest": _digest(
                {
                    "canvas": canvas,
                    "layers": [
                        {
                            "id": layer.id,
                            "textureVersion": layer.texture_version,
                            "transformVersion": layer.transform_version,
                        }
                        for layer in scene.flat_layers
                    ],
                    "selection": selection,
                    "documentMetadata": metadata_revision(self._service),
                }
            ),
            "disallowedPayloads": [
                "api_key",
                "session_token",
                "raw_provider_request_body",
                "full_resolution_layer_binary",
                "live_qimage",
                "qobject",
                "canvas_private_state",
            ],
        }
        if (
            args.get("includePanelFrames")
            and getattr(self._service, "canvas_service", None) is not None
        ):
            from .panel_frame_changeset import frame_geometry_summary  # noqa: PLC0415

            context["panelFrames"] = frame_geometry_summary(self._service.canvas_service)
        self._last_context = scrub_secrets(context)
        return self._last_context

    def plan_canvas_edit(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        prompt = _required_prompt(args.get("prompt") or args.get("request"))
        client_request_id = args.get("clientRequestId")
        if (
            client_request_id is not None
            and len(str(client_request_id)) > MAX_CLIENT_REQUEST_ID_LENGTH
        ):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'clientRequestId' is too long.",
                code=-32602,
                details={"maxLength": MAX_CLIENT_REQUEST_ID_LENGTH},
            )

        requested_planner = (
            str(args.get("plannerProvider") or args.get("planner") or "deterministic")
            .strip()
            .lower()
            or "deterministic"
        )
        context_arg = args.get("editContext") or args.get("context")
        if requested_planner == "llm_read_only":
            context = strip_planner_payloads(self.get_edit_context({"includeThumbnail": False}))
        else:
            context = (
                scrub_secrets(strip_planner_payloads(dict(context_arg)))
                if isinstance(context_arg, Mapping)
                else self.get_edit_context({"includeThumbnail": False})
            )
        intent = _infer_intent(prompt)
        blocked_reasons = _plan_blocked_reasons(prompt, context, intent, self._service)
        clarification_questions = _clarification_questions(prompt, context, intent)
        target_resolution = _target_resolution(prompt, context, intent)
        clarification = _actionable_clarification(
            prompt,
            context,
            intent,
            target_resolution,
        )
        state = "awaiting_clarification" if clarification else "planned"
        now = _now()
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        plan_id = f"cplan_{uuid.uuid4().hex[:12]}"
        if clarification is not None and not clarification_questions:
            clarification_questions = [
                {
                    "id": clarification.get("clarificationId"),
                    "question": clarification.get("question"),
                    "reason": clarification.get("reason"),
                    "kind": clarification.get("kind"),
                }
            ]
        plan = {
            "schemaVersion": PLAN_SCHEMA_VERSION,
            "planId": plan_id,
            "runId": run_id,
            "createdAt": now,
            "status": state,
            "readOnly": True,
            "documentMutation": "none",
            "mutationAllowed": False,
            "externalModel": {
                "called": False,
                "reason": "phase0_local_rule_based_planner",
                "privacyAllowed": bool(
                    getattr(self._service.safety, "external_services_allowed", False)
                ),
                "credentialReadPerformed": False,
            },
            "plannerProvider": "deterministic",
            "externalPlanner": self._planner_metadata(
                requested=requested_planner,
                provider="deterministic",
                fallback_reason=(None if requested_planner == "deterministic" else "not_requested"),
            ),
            "sourcePromptDigest": _sha256(prompt),
            "contextId": context.get("contextId"),
            "contextDigest": context.get("contextDigest"),
            "intent": intent,
            "target": _plan_target(context),
            "targetResolution": target_resolution,
            "targetCandidates": list(target_resolution.get("candidateTargets") or []),
            "protectedCandidates": list(target_resolution.get("protectedTargets") or []),
            "steps": _plan_steps(intent, context),
            "requiredTools": [
                "loomstroke_get_edit_context",
                "loomstroke_plan_canvas_edit",
            ],
            "allowedTools": self.tool_registry.list_tools(),
            "requiredPermissions": _required_permissions(intent),
            "requiresApprovalBeforeApply": intent["kind"] != "inspect",
            "handoff": _handoff_card(intent, prompt, context),
            "memory": _style_memory_context(self._style_memory_entries),
            "blockedReasons": blocked_reasons,
            "clarificationQuestions": clarification_questions,
            "clarification": clarification,
            "safety": {
                "confirmationRequiredForApply": intent["kind"] != "inspect",
                "privacyGateChecked": True,
                "credentialReadPerformed": False,
                "rawPromptIncluded": False,
                "rawBinaryIncluded": False,
            },
        }
        if requested_planner == "llm_read_only":
            plan = self._apply_external_planner_if_available(prompt, context, plan)
            state = str(plan.get("status") or state)
        else:
            self._last_planner_provider = "deterministic"
        run = AgentRunRecord(
            run_id=run_id,
            state=state,
            created_at=now,
            updated_at=now,
            context=context,
            plan=scrub_secrets(plan),
            log=[
                {
                    "at": now,
                    "event": "plan_created",
                    "state": state,
                    "externalModelCalled": bool(
                        (plan.get("externalModel") or {}).get("called")
                        if isinstance(plan.get("externalModel"), Mapping)
                        else False
                    ),
                    "plannerProvider": plan.get("plannerProvider"),
                    "documentMutation": "none",
                }
            ],
        )
        self._runs[run_id] = run
        return run.plan or plan

    def planner_readiness(self) -> dict[str, Any]:
        provider = getattr(self._service, "_canvas_agent_planner_provider", None)
        external_allowed = bool(getattr(self._service.safety, "external_services_allowed", False))
        blocked_reason = None
        if provider is None:
            blocked_reason = "gateway_unavailable"
        elif not external_allowed:
            blocked_reason = "privacy_gate_disabled"
        return {
            "externalPlannerAvailable": provider is not None,
            "externalPlannerEnabled": bool(provider is not None and external_allowed),
            "externalPlannerBlockedReason": blocked_reason,
            "readOnlyToolAllowlistHash": read_only_tool_allowlist_hash(),
            "lastPlannerProvider": self._last_planner_provider,
            "plannerCredentialReadPerformed": self._planner_credential_read_performed,
        }

    def _apply_external_planner_if_available(
        self,
        prompt: str,
        context: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> dict[str, Any]:
        provider = getattr(self._service, "_canvas_agent_planner_provider", None)
        if not bool(getattr(self._service.safety, "external_services_allowed", False)):
            return self._planner_fallback(plan, "privacy_gate_disabled")
        if provider is None:
            return self._planner_fallback(plan, "gateway_unavailable")
        tools = CanvasAgentReadOnlyPlannerTools(self._service)
        try:
            candidate = provider.plan(prompt, context, tools)
            public_fields = validate_canvas_edit_plan_candidate(candidate)
            public_fields = _omit_prompt_text(strip_planner_payloads(public_fields), prompt)
        except McpStructuredError as exc:
            return self._planner_fallback(plan, _planner_error_code(exc))
        except Exception:
            return self._planner_fallback(plan, "planner_provider_failed")

        try:
            updated = dict(plan)
            updated.update(public_fields)
            updated["status"] = (
                "awaiting_clarification"
                if _planner_sequence(updated.get("clarificationQuestions"))
                else "planned"
            )
            updated["externalModel"] = {
                "called": True,
                "reason": "llm_read_only_planner",
                "provider": "llm_read_only",
                "privacyAllowed": True,
                "credentialReadPerformed": False,
            }
            updated["plannerProvider"] = "llm_read_only"
            updated["externalPlanner"] = self._planner_metadata(
                requested="llm_read_only",
                provider="llm_read_only",
                fallback_reason=None,
            )
            safety = dict(
                updated.get("safety") if isinstance(updated.get("safety"), Mapping) else {}
            )
            safety["credentialReadPerformed"] = False
            safety["rawPromptIncluded"] = False
            safety["rawBinaryIncluded"] = False
            updated["safety"] = safety
            self._last_planner_provider = "llm_read_only"
            self._planner_credential_read_performed = False
            return scrub_secrets(updated)
        except Exception:
            return self._planner_fallback(plan, "planner_provider_failed")

    def _planner_fallback(
        self,
        plan: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        updated = dict(plan)
        updated["plannerProvider"] = "deterministic"
        updated["externalPlanner"] = self._planner_metadata(
            requested="llm_read_only",
            provider="deterministic",
            fallback_reason=reason,
        )
        external_model = dict(
            updated.get("externalModel")
            if isinstance(updated.get("externalModel"), Mapping)
            else {}
        )
        external_model.update(
            {
                "called": False,
                "reason": f"llm_read_only_{reason}_fallback",
                "privacyAllowed": bool(
                    getattr(self._service.safety, "external_services_allowed", False)
                ),
                "credentialReadPerformed": False,
            }
        )
        updated["externalModel"] = external_model
        self._last_planner_provider = "deterministic"
        self._planner_credential_read_performed = False
        return scrub_secrets(updated)

    def _planner_metadata(
        self,
        *,
        requested: str,
        provider: str,
        fallback_reason: str | None,
    ) -> dict[str, Any]:
        readiness = self.planner_readiness()
        return {
            "schemaVersion": "canvas_agent_planner_trace.v1",
            "requestedProvider": requested,
            "provider": provider,
            "fallback": fallback_reason is not None,
            "blockedReason": ({"code": fallback_reason} if fallback_reason else None),
            "externalPlannerAvailable": readiness["externalPlannerAvailable"],
            "externalPlannerEnabled": readiness["externalPlannerEnabled"],
            "readOnlyToolAllowlistHash": readiness["readOnlyToolAllowlistHash"],
            "credentialReadPerformed": False,
            "rawPromptIncluded": False,
        }

    def current_context_resource(self) -> dict[str, Any]:
        return self.get_edit_context({"includeThumbnail": False})

    def list_runs(self) -> list[dict[str, Any]]:
        return [record.summary() for record in self._runs.values()]

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._run(run_id).to_schema()

    def get_run_plan(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        if run.plan is None:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent run does not have a plan yet.",
                details={"runId": run_id},
            )
        return run.plan

    def get_run_clarification(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        plan = run.plan if isinstance(run.plan, Mapping) else {}
        clarification = (
            plan.get("clarification") if isinstance(plan.get("clarification"), Mapping) else {}
        )
        if not clarification:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent run does not have an actionable clarification.",
                details={"runId": run_id},
            )
        return scrub_secrets(dict(clarification))

    def get_run_clarification_answer(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        plan = run.plan if isinstance(run.plan, Mapping) else {}
        answer = (
            plan.get("resolvedClarification")
            if isinstance(plan.get("resolvedClarification"), Mapping)
            else {}
        )
        if not answer:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent run does not have a clarification answer yet.",
                details={"runId": run_id},
            )
        return scrub_secrets(dict(answer))

    def create_canvas_changeset(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        if isinstance(args.get("candidateStack"), Mapping):
            return self._create_selected_candidate_changeset(args)
        plan = self._resolve_plan(args)
        context = self._resolve_context(args)
        existing_asset_ids = self._existing_asset_ids()
        changeset = build_canvas_changeset(
            plan,
            context,
            existing_asset_ids=existing_asset_ids,
        )
        validation = validate_canvas_changeset(
            changeset,
            context,
            existing_asset_ids=existing_asset_ids,
        )
        changeset["validation"] = validation
        if validation.get("status") == "blocked":
            changeset["status"] = "blocked"
        changeset = scrub_secrets(changeset)
        changeset_id = str(changeset.get("changesetId") or "")
        if changeset_id:
            self._changesets[changeset_id] = changeset
        self._attach_changeset_to_run(changeset)
        return changeset

    def build_candidate_stack(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        job = args.get("job")
        if not isinstance(job, Mapping):
            job_id = str(args.get("jobId") or "").strip()
            if not job_id:
                raise McpStructuredError(
                    ReasonCode.INVALID_ARGUMENT,
                    "'job' or 'jobId' is required.",
                    code=-32602,
                    details={"arguments": ["job", "jobId"]},
                )
            getter = getattr(self._service, "get_job_status", None)
            if not callable(getter):
                raise McpStructuredError(
                    ReasonCode.CAPABILITY_UNAVAILABLE,
                    "Job status lookup is not available for candidate stack creation.",
                    details={"jobId": job_id},
                )
            job = getter(job_id)
        _validate_candidate_job(job)
        asset_ids = _candidate_asset_ids(args, job)
        assets: list[Mapping[str, Any]] = []
        getter = getattr(self._service, "get_asset", None)
        loader = getattr(self._service, "_qimage_from_asset", None)
        if not callable(getter):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Asset metadata lookup is not available for candidate stack creation.",
                details={"jobId": job.get("jobId")},
            )
        for asset_id in asset_ids:
            _validate_candidate_asset_id_for_job(asset_id, job)
            asset = getter(asset_id)
            if isinstance(asset, Mapping):
                _validate_candidate_asset_for_job(asset, job)
                if callable(loader):
                    loader(asset_id)
                assets.append(asset)
        run_id = str(args.get("runId") or job.get("runId") or "").strip() or None
        return build_canvas_candidate_stack(run_id=run_id, job=job, assets=assets)

    def select_candidate(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        stack = args.get("candidateStack")
        if not isinstance(stack, Mapping):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'candidateStack' is required.",
                code=-32602,
                details={"argument": "candidateStack"},
            )
        return with_selected_candidate(stack, str(args.get("selectedCandidateId") or ""))

    def answer_canvas_clarification(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve an actionable clarification without previewing or applying."""

        args = dict(arguments or {})
        run_id = _required_argument_text(args, "runId")
        clarification_id = _required_argument_text(args, "clarificationId")
        selected_option_id = _required_argument_text(args, "selectedOptionId")
        payload = args.get("payload") if isinstance(args.get("payload"), Mapping) else {}

        run = self._run(run_id)
        plan = run.plan if isinstance(run.plan, Mapping) else {}
        clarification = (
            plan.get("clarification") if isinstance(plan.get("clarification"), Mapping) else {}
        )
        if not clarification:
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "Canvas Agent run does not have an actionable clarification.",
                details={"runId": run_id, "clarificationId": clarification_id},
            )
        if str(clarification.get("clarificationId") or "") != clarification_id:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent clarification was not found for this run.",
                details={"runId": run_id, "clarificationId": clarification_id},
            )
        option = _clarification_option(clarification, selected_option_id)
        action = str(option.get("action") or "").strip()
        now = _now()

        if action == "discard":
            run.state = "discarded"
            run.updated_at = now
            updated_plan = dict(plan)
            updated_plan["status"] = "discarded"
            updated_plan["clarification"] = {
                **dict(clarification),
                "status": "discarded",
                "selectedOptionId": selected_option_id,
            }
            run.plan = scrub_secrets(updated_plan)
            run.changeset = None
            run.preview = None
            run.log.append(
                {
                    "at": now,
                    "event": "clarification_discarded",
                    "clarificationId": clarification_id,
                    "selectedOptionId": selected_option_id,
                    "documentMutation": "none",
                }
            )
            return scrub_secrets(
                {
                    "schemaVersion": CLARIFICATION_ANSWER_RESULT_SCHEMA_VERSION,
                    "runId": run_id,
                    "clarificationId": clarification_id,
                    "selectedOptionId": selected_option_id,
                    "status": "discarded",
                    "plan": run.plan,
                    "documentMutation": "none",
                    "historyMutation": "none",
                    "assetWrite": "none",
                    "nextActions": [],
                    "safety": _clarification_answer_safety(),
                }
            )

        if action == "ui_action":
            run.updated_at = now
            run.log.append(
                {
                    "at": now,
                    "event": "clarification_requires_user_action",
                    "clarificationId": clarification_id,
                    "selectedOptionId": selected_option_id,
                    "documentMutation": "none",
                }
            )
            return scrub_secrets(
                {
                    "schemaVersion": CLARIFICATION_ANSWER_RESULT_SCHEMA_VERSION,
                    "runId": run_id,
                    "clarificationId": clarification_id,
                    "selectedOptionId": selected_option_id,
                    "status": "requires_user_action",
                    "userAction": scrub_secrets(option.get("userAction") or {}),
                    "message": option.get("description") or option.get("label"),
                    "documentMutation": "none",
                    "historyMutation": "none",
                    "assetWrite": "none",
                    "nextActions": [],
                    "safety": _clarification_answer_safety(),
                }
            )

        if action != "patch_plan":
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Clarification option action is not supported.",
                code=-32602,
                details={
                    "runId": run_id,
                    "clarificationId": clarification_id,
                    "selectedOptionId": selected_option_id,
                    "action": action,
                },
            )

        updated_plan = _patch_plan_for_clarification(
            plan,
            run.context,
            clarification,
            option,
            payload,
        )
        existing_asset_ids = self._existing_asset_ids()
        changeset = build_canvas_changeset(
            updated_plan,
            run.context,
            existing_asset_ids=existing_asset_ids,
        )
        validation = validate_canvas_changeset(
            changeset,
            run.context,
            existing_asset_ids=existing_asset_ids,
        )
        changeset["validation"] = validation
        if validation.get("status") == "blocked":
            changeset["status"] = "blocked"
        changeset = scrub_secrets(changeset)
        changeset_id = str(changeset.get("changesetId") or "")
        if changeset_id:
            self._changesets[changeset_id] = changeset

        run.plan = scrub_secrets(updated_plan)
        run.state = "blocked" if validation.get("status") == "blocked" else "planned"
        run.updated_at = _now()
        run.preview = None
        run.apply_result = None
        self._attach_changeset_to_run(changeset)
        run.log.append(
            {
                "at": run.updated_at,
                "event": "clarification_resolved",
                "clarificationId": clarification_id,
                "selectedOptionId": selected_option_id,
                "changesetId": changeset.get("changesetId"),
                "validationStatus": validation.get("status"),
                "documentMutation": "none",
            }
        )
        status = "blocked" if validation.get("status") == "blocked" else "resolved"
        return scrub_secrets(
            {
                "schemaVersion": CLARIFICATION_ANSWER_RESULT_SCHEMA_VERSION,
                "runId": run_id,
                "clarificationId": clarification_id,
                "selectedOptionId": selected_option_id,
                "status": status,
                "plan": run.plan,
                "changeset": changeset,
                "changesetId": changeset.get("changesetId"),
                "documentMutation": "none",
                "historyMutation": "none",
                "assetWrite": "none",
                "nextActions": (
                    ["preview_canvas_changeset"]
                    if status == "resolved" and changeset.get("operations")
                    else []
                ),
                "safety": _clarification_answer_safety(),
            }
        )

    def _create_selected_candidate_changeset(self, args: Mapping[str, Any]) -> dict[str, Any]:
        stack = args.get("candidateStack")
        if not isinstance(stack, Mapping):
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'candidateStack' is required.",
                code=-32602,
                details={"argument": "candidateStack"},
            )
        plan = self._resolve_plan(args)
        context = self._resolve_context(args)
        candidate = selected_candidate(stack, str(args.get("selectedCandidateId") or ""))
        self._validate_selected_candidate_stack_asset(stack, candidate)
        changeset = build_selected_candidate_changeset(
            plan=plan,
            context=context,
            candidate_stack=stack,
            selected_candidate_id=str(args.get("selectedCandidateId") or ""),
        )
        existing_asset_ids = self._existing_asset_ids()
        validation = validate_canvas_changeset(
            changeset,
            context,
            existing_asset_ids=existing_asset_ids,
        )
        changeset["validation"] = validation
        if validation.get("status") == "blocked":
            changeset["status"] = "blocked"
        changeset = scrub_secrets(changeset)
        changeset_id = str(changeset.get("changesetId") or "")
        if changeset_id:
            self._changesets[changeset_id] = changeset
        self._attach_changeset_to_run(changeset)
        return changeset

    def _validate_selected_candidate_stack_asset(
        self,
        stack: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> None:
        job_id = str(stack.get("jobId") or "").strip()
        asset_id = str(candidate.get("assetId") or "").strip()
        getter = getattr(self._service, "get_job_status", None)
        asset_getter = getattr(self._service, "get_asset", None)
        loader = getattr(self._service, "_qimage_from_asset", None)
        if not job_id or not callable(getter):
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "Candidate changeset requires a generation job reference.",
                details={"stackId": stack.get("stackId"), "code": "candidate_job_required"},
            )
        if not asset_id or not callable(asset_getter):
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "Candidate changeset requires a selected asset reference.",
                details={"stackId": stack.get("stackId"), "code": "candidate_asset_required"},
            )
        job = getter(job_id)
        _validate_candidate_job(job)
        _validate_candidate_asset_id_for_job(asset_id, job)
        asset = asset_getter(asset_id)
        if not isinstance(asset, Mapping):
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "Candidate asset metadata is not available.",
                details={"stackId": stack.get("stackId"), "assetId": asset_id},
            )
        _validate_candidate_asset_for_job(asset, job)
        if callable(loader):
            loader(asset_id)

    def preview_canvas_changeset(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        changeset = self._resolve_changeset(args)
        context = self._resolve_context(args)
        existing_asset_ids = self._existing_asset_ids()
        validation = validate_canvas_changeset(
            changeset,
            context,
            existing_asset_ids=existing_asset_ids,
        )
        from .panel_frame_preview import prepare_frame_preview  # noqa: PLC0415

        panel_frame_preview = prepare_frame_preview(
            self._service.canvas_service,
            changeset,
            validation,
            asset_loader=getattr(self._service, "_qimage_from_asset", None),
        )
        preview = build_canvas_changeset_preview(
            changeset,
            context,
            validation=validation,
        )
        if panel_frame_preview:
            preview["panelFramePreview"] = panel_frame_preview
        preview["visualPreview"] = self._build_visual_preview(changeset, context, preview)
        preview = scrub_secrets(preview)
        self._previews[str(preview["previewId"])] = preview
        safe_changeset = scrub_secrets(strip_canvas_agent_payloads(changeset))

        visual_preview = preview.get("visualPreview")
        visual_preview_id = (
            visual_preview.get("visualPreviewId") if isinstance(visual_preview, Mapping) else None
        )
        updated_changeset = scrub_secrets(
            {
                **dict(safe_changeset),
                "updatedAt": _now(),
                "status": "blocked" if preview.get("blocked") else "previewed",
                "validation": validation,
                "preview": {
                    "status": "blocked" if preview.get("blocked") else "created",
                    "previewId": preview.get("previewId"),
                    "visualPreviewId": visual_preview_id,
                    "mode": preview.get("mode"),
                },
                "approval": {
                    "required": bool(_operation_list(changeset)),
                    "ticketId": (
                        (preview.get("approvalTicket") or {}).get("ticketId")
                        if isinstance(preview.get("approvalTicket"), Mapping)
                        else None
                    ),
                    "status": (
                        "issued"
                        if isinstance(preview.get("approvalTicket"), Mapping)
                        else "not_issued"
                    ),
                },
            }
        )
        changeset_id = str(updated_changeset.get("changesetId") or "")
        if changeset_id:
            self._changesets[changeset_id] = updated_changeset
        ticket = preview.get("approvalTicket")
        if isinstance(ticket, Mapping) and ticket.get("ticketId"):
            self._approval_tickets[str(ticket["ticketId"])] = dict(ticket)
        self._attach_preview_to_run(updated_changeset, preview)
        return preview

    def canvas_changeset_apply_confirmation_details(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return safe details for the transport confirmation prompt."""

        changeset, _context, validation, ticket = self._prepare_apply_request(arguments)
        return scrub_secrets(
            {
                "schemaVersion": "canvas_changeset_apply_confirmation.v1",
                "changesetId": changeset.get("changesetId"),
                "runId": changeset.get("runId"),
                "operationCount": len(_operation_list(changeset)),
                "baseDocumentRevision": changeset.get("baseDocumentRevision"),
                "approvalTicketId": ticket.get("ticketId"),
                "approvalPreviewId": ticket.get("previewId"),
                "validationStatus": validation.get("status"),
                "documentMutation": "command_backed",
            }
        )

    def apply_canvas_changeset(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        changeset, _context, _validation, _ticket = self._prepare_apply_request(arguments)
        canvas = getattr(self._service, "canvas_service", None)
        submit = getattr(canvas, "submit", None)
        if canvas is None or not callable(submit):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas service is not available for CanvasChangeset apply.",
                details={"changesetId": changeset.get("changesetId")},
            )
        asset_images = self._asset_images_for_apply(changeset)
        command = CanvasChangesetApplyCommand(changeset, asset_images=asset_images)
        if not bool(submit(command)):
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "CanvasChangeset apply failed and was rolled back.",
                details={
                    "changesetId": changeset.get("changesetId"),
                    "warnings": command.warnings,
                },
            )
        post_context = self.get_edit_context({"includeThumbnail": False})
        history_label = _history_label(changeset)
        result = build_apply_result(
            changeset,
            command,
            post_context=post_context,
            history_label=history_label,
        )
        apply_id = str(result.get("applyId") or "")
        if apply_id:
            self._apply_results[apply_id] = result

        updated_changeset = scrub_secrets(
            {
                **dict(changeset),
                "updatedAt": _now(),
                "status": "applied",
                "apply": {
                    "status": "applied",
                    "applyId": result.get("applyId"),
                    "undoGroupId": result.get("undoGroupId"),
                    "historyLabel": history_label,
                },
            }
        )
        changeset_id = str(updated_changeset.get("changesetId") or "")
        if changeset_id:
            self._changesets[changeset_id] = updated_changeset
        self._record_style_memory_from_apply(updated_changeset, result)
        self._attach_apply_to_run(updated_changeset, result)
        return result

    def verify_canvas_edit(
        self,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        apply_result = self._resolve_apply_result(args)
        context = self._resolve_context(args)
        return build_verification_result(apply_result, context)

    def get_run_changeset(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        if run.changeset is None:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent run does not have a changeset yet.",
                details={"runId": run_id},
            )
        return run.changeset

    def get_run_preview(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        if run.preview is None:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent run does not have a preview yet.",
                details={"runId": run_id},
            )
        return run.preview

    def get_run_apply_result(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        if run.apply_result is None:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent run does not have an apply result yet.",
                details={"runId": run_id},
            )
        return run.apply_result

    def get_changeset(self, changeset_id: str) -> dict[str, Any]:
        try:
            return self._changesets[changeset_id]
        except KeyError as exc:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent changeset was not found.",
                details={"changesetId": changeset_id},
            ) from exc

    def get_preview(self, preview_id: str) -> dict[str, Any]:
        try:
            return self._previews[preview_id]
        except KeyError as exc:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent preview was not found.",
                details={"previewId": preview_id},
            ) from exc

    def get_apply_result(self, apply_id: str) -> dict[str, Any]:
        try:
            return self._apply_results[apply_id]
        except KeyError as exc:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent apply result was not found.",
                details={"applyId": apply_id},
            ) from exc

    def _run(self, run_id: str) -> AgentRunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "Canvas Agent run was not found.",
                details={"runId": run_id},
            ) from exc

    def _resolve_plan(self, args: Mapping[str, Any]) -> dict[str, Any]:
        plan = args.get("plan")
        if isinstance(plan, Mapping):
            return scrub_secrets(dict(plan))
        run_id = args.get("runId")
        if isinstance(run_id, str) and run_id.strip():
            return self.get_run_plan(run_id.strip())
        plan_id = args.get("planId")
        if isinstance(plan_id, str) and plan_id.strip():
            for run in self._runs.values():
                if (run.plan or {}).get("planId") == plan_id.strip():
                    return run.plan or {}
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'plan', 'runId', or 'planId' is required.",
            code=-32602,
            details={"arguments": ["plan", "runId", "planId"]},
        )

    def _resolve_changeset(self, args: Mapping[str, Any]) -> dict[str, Any]:
        changeset = args.get("changeset")
        if isinstance(changeset, Mapping):
            return scrub_secrets(dict(changeset))
        changeset_id = args.get("changesetId")
        if isinstance(changeset_id, str) and changeset_id.strip():
            return self.get_changeset(changeset_id.strip())
        run_id = args.get("runId")
        if isinstance(run_id, str) and run_id.strip():
            return self.get_run_changeset(run_id.strip())
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'changeset', 'changesetId', or 'runId' is required.",
            code=-32602,
            details={"arguments": ["changeset", "changesetId", "runId"]},
        )

    def _resolve_context(self, args: Mapping[str, Any]) -> dict[str, Any]:
        context_arg = args.get("editContext") or args.get("context")
        if isinstance(context_arg, Mapping):
            return scrub_secrets(dict(context_arg))
        return self.get_edit_context({"includeThumbnail": False})

    def _prepare_apply_request(
        self,
        arguments: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        args = dict(arguments or {})
        changeset = self._resolve_changeset(args)
        context = self._resolve_context(args)
        existing_asset_ids = self._existing_asset_ids()
        validation = validate_canvas_changeset(
            changeset,
            context,
            existing_asset_ids=existing_asset_ids,
        )
        if validation.get("status") == "blocked":
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "CanvasChangeset validation failed before apply.",
                details={
                    "changesetId": changeset.get("changesetId"),
                    "validation": validation,
                },
            )
        unsupported = unsupported_apply_operations(changeset)
        if unsupported:
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "CanvasChangeset contains operations not supported by Section 4 apply.",
                details={
                    "changesetId": changeset.get("changesetId"),
                    "unsupportedOperations": unsupported,
                },
            )
        ticket = self._resolve_approval_ticket(args, changeset)
        self._validate_approval_ticket(ticket, changeset)
        return changeset, context, validation, ticket

    def _resolve_approval_ticket(
        self,
        args: Mapping[str, Any],
        changeset: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_ticket = args.get("approvalTicket")
        if isinstance(raw_ticket, Mapping):
            return scrub_secrets(dict(raw_ticket))

        ticket_id = args.get("approvalTicketId") or args.get("ticketId")
        if not isinstance(ticket_id, str) or not ticket_id.strip():
            approval = changeset.get("approval")
            if isinstance(approval, Mapping):
                ticket_id = approval.get("ticketId")
        if isinstance(ticket_id, str) and ticket_id.strip():
            cached = self._approval_tickets.get(ticket_id.strip())
            if cached is not None:
                return scrub_secrets(dict(cached))
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Approval ticket is not known to this Canvas Agent runtime.",
                details={
                    "changesetId": changeset.get("changesetId"),
                    "ticketId": ticket_id.strip(),
                },
            )
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Approval ticket is required before applying a CanvasChangeset.",
            details={"changesetId": changeset.get("changesetId")},
        )

    def _validate_approval_ticket(
        self,
        ticket: Mapping[str, Any],
        changeset: Mapping[str, Any],
    ) -> None:
        changeset_id = str(changeset.get("changesetId") or "")
        if str(ticket.get("changesetId") or "") != changeset_id:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Approval ticket does not match the CanvasChangeset.",
                details={
                    "changesetId": changeset_id,
                    "ticketChangesetId": ticket.get("changesetId"),
                },
            )
        expected_hash = canvas_changeset_hash(changeset)
        if str(ticket.get("changesetHash") or "") != expected_hash:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Approval ticket hash does not match the CanvasChangeset.",
                details={"changesetId": changeset_id},
            )
        ticket_revision = str(ticket.get("baseDocumentRevision") or "")
        changeset_revision = str(changeset.get("baseDocumentRevision") or "")
        if ticket_revision != changeset_revision:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Approval ticket base revision does not match the CanvasChangeset.",
                details={
                    "changesetId": changeset_id,
                    "ticketBaseDocumentRevision": ticket_revision,
                    "baseDocumentRevision": changeset_revision,
                },
            )
        expires_at = _parse_datetime(ticket.get("expiresAt"))
        if expires_at is None or expires_at <= datetime.now(UTC):
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Approval ticket has expired.",
                details={
                    "changesetId": changeset_id,
                    "ticketId": ticket.get("ticketId"),
                    "expiresAt": ticket.get("expiresAt"),
                },
            )

    def _resolve_apply_result(self, args: Mapping[str, Any]) -> dict[str, Any]:
        apply_result = args.get("applyResult")
        if isinstance(apply_result, Mapping):
            return scrub_secrets(dict(apply_result))
        apply_id = args.get("applyId")
        if isinstance(apply_id, str) and apply_id.strip():
            return self.get_apply_result(apply_id.strip())
        run_id = args.get("runId")
        if isinstance(run_id, str) and run_id.strip():
            return self.get_run_apply_result(run_id.strip())
        changeset_id = args.get("changesetId")
        if isinstance(changeset_id, str) and changeset_id.strip():
            for result in self._apply_results.values():
                if result.get("changesetId") == changeset_id.strip():
                    return result
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'applyResult', 'applyId', 'runId', or 'changesetId' is required.",
            code=-32602,
            details={"arguments": ["applyResult", "applyId", "runId", "changesetId"]},
        )

    def _asset_images_for_apply(self, changeset: Mapping[str, Any]) -> dict[str, Any]:
        for index, operation in enumerate(_operation_list(changeset)):
            op_name = str(operation.get("op") or operation.get("kind") or "")
            if (
                op_name == "submit_generation_to_new_layer"
                and not str(
                    operation.get("assetId") or operation.get("selectedAssetId") or ""
                ).strip()
            ):
                raise McpStructuredError(
                    ReasonCode.CONFLICT,
                    "Generation candidate operation requires a selected asset before apply.",
                    details={"changesetId": changeset.get("changesetId"), "index": index},
                )
        asset_ids = asset_ids_for_apply(changeset)
        if not asset_ids:
            return {}
        loader = getattr(self._service, "_qimage_from_asset", None)
        if not callable(loader):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Asset image loader is not available for CanvasChangeset apply.",
                details={"changesetId": changeset.get("changesetId")},
            )
        images: dict[str, Any] = {}
        for asset_id in asset_ids:
            images[asset_id] = loader(asset_id)
        return images

    def _existing_asset_ids(self) -> list[str]:
        return [
            str(asset.get("assetId") or "")
            for asset in _safe_list(self._service.list_assets)
            if str(asset.get("assetId") or "")
        ]

    def _attach_changeset_to_run(self, changeset: Mapping[str, Any]) -> None:
        run_id = str(changeset.get("runId") or "")
        if not run_id or run_id not in self._runs:
            return
        run = self._runs[run_id]
        run.changeset = dict(changeset)
        run.updated_at = _now()
        run.log.append(
            {
                "at": run.updated_at,
                "event": "changeset_created",
                "changesetId": changeset.get("changesetId"),
                "documentMutation": "none",
            }
        )

    def _attach_preview_to_run(
        self,
        changeset: Mapping[str, Any],
        preview: Mapping[str, Any],
    ) -> None:
        run_id = str(changeset.get("runId") or "")
        if not run_id or run_id not in self._runs:
            return
        run = self._runs[run_id]
        run.changeset = dict(changeset)
        run.preview = dict(preview)
        run.updated_at = _now()
        visual_preview = preview.get("visualPreview")
        visual_preview_id = (
            visual_preview.get("visualPreviewId") if isinstance(visual_preview, Mapping) else None
        )
        run.log.append(
            {
                "at": run.updated_at,
                "event": "preview_created",
                "changesetId": changeset.get("changesetId"),
                "previewId": preview.get("previewId"),
                "visualPreviewId": visual_preview_id,
                "blocked": bool(preview.get("blocked")),
                "documentMutation": "none",
            }
        )

    def _build_visual_preview(
        self,
        changeset: Mapping[str, Any],
        context: Mapping[str, Any],
        preview: Mapping[str, Any],
    ) -> dict[str, Any]:
        renderer_available = bool(
            getattr(self._service, "_canvas_agent_visual_preview_renderer_available", True)
        )
        try:
            previewer = CanvasAgentVisualPreviewer(
                register_asset=self._register_visual_preview_asset,
                renderer_available=renderer_available,
            )
            return previewer.build(changeset, context, preview)
        except Exception:  # pragma: no cover - defensive preview fallback
            return build_visual_preview_metadata_fallback(changeset, context, preview)

    def _register_visual_preview_asset(
        self,
        payload: bytes,
        dimensions: Mapping[str, int],
        metadata: Mapping[str, Any],
    ) -> str | None:
        register = getattr(self._service, "_register_generated_asset", None)
        if not callable(register):
            return None
        return register(
            payload,
            source="canvas_visual_preview",
            dimensions={key: int(value) for key, value in dimensions.items()},
            metadata=metadata,
        )

    def _attach_apply_to_run(
        self,
        changeset: Mapping[str, Any],
        apply_result: Mapping[str, Any],
    ) -> None:
        run_id = str(changeset.get("runId") or "")
        if not run_id or run_id not in self._runs:
            return
        run = self._runs[run_id]
        run.changeset = dict(changeset)
        run.apply_result = dict(apply_result)
        run.state = "applied"
        run.updated_at = _now()
        run.log.append(
            {
                "at": run.updated_at,
                "event": "changeset_applied",
                "changesetId": changeset.get("changesetId"),
                "applyId": apply_result.get("applyId"),
                "documentMutation": "command_backed",
            }
        )

    def _record_style_memory_from_apply(
        self,
        changeset: Mapping[str, Any],
        apply_result: Mapping[str, Any],
    ) -> None:
        entries = _style_memory_entries_from_apply(changeset, apply_result)
        if not entries:
            return
        self._style_memory_entries.extend(entries)
        self._style_memory_entries = self._style_memory_entries[-MAX_STYLE_MEMORY_ENTRIES:]


def _layer_context(layer: Any, index: int) -> dict[str, Any]:
    image = getattr(layer, "image", None)
    dimensions = None
    if image is not None and not image.isNull():
        dimensions = {"width": int(image.width()), "height": int(image.height())}
    layer_type = str(getattr(layer, "layer_type", "raster") or "raster")
    vector_kind = str(getattr(layer, "vector_kind", "draw") or "draw")
    name = str(getattr(layer, "name", "") or "")
    role_hint_details = _role_hint_details(name, layer_type, vector_kind, index)
    return {
        "layerId": str(getattr(layer, "id", "") or ""),
        "name": name,
        "index": index,
        "parentId": getattr(layer, "parent_id", None),
        "layerType": layer_type,
        "vectorKind": vector_kind,
        "roleHints": [detail["role"] for detail in role_hint_details],
        "roleHintDetails": role_hint_details,
        "visible": bool(getattr(layer, "visible", True)),
        "opacity": float(getattr(layer, "opacity", 1.0)),
        "blendMode": to_jsonable(getattr(layer, "blend_mode", "normal")),
        "locked": bool(getattr(layer, "locked", False)),
        "alphaLock": bool(getattr(layer, "alpha_lock", False)),
        "clipping": bool(getattr(layer, "clipping", False)),
        "offset": {
            "x": int(getattr(layer, "offset_x", 0)),
            "y": int(getattr(layer, "offset_y", 0)),
        },
        "textureVersion": int(getattr(layer, "texture_version", 0)),
        "transformVersion": int(getattr(layer, "transform_version", 0)),
        "vectorObjectCount": len(getattr(layer, "vector_objects", []) or []),
        "childrenCount": len(getattr(layer, "children", []) or []),
        "imageDimensions": dimensions,
        "hasLayerMask": getattr(layer, "layer_mask", None) is not None,
        "maskEnabled": bool(getattr(layer, "mask_enabled", True)),
        "effectType": getattr(layer, "filter_type", getattr(layer, "effect_type", None)),
        "hasEffectMask": getattr(layer, "filter_mask", getattr(layer, "effect_mask", None))
        is not None,
        "hasAiEffectState": getattr(layer, "ai_effect", None) is not None,
        "has3dScene": getattr(layer, "scene_3d", None) is not None,
    }


def _selection_context(scene: Any) -> dict[str, Any]:
    selection = scene.selection
    mask = selection.mask
    preview = selection.preview_image
    bounds = qrect_to_tuple(selection.bounds)
    mask_dimensions = (
        {"width": int(mask.width()), "height": int(mask.height())}
        if mask is not None and not mask.isNull()
        else None
    )
    preview_dimensions = (
        {"width": int(preview.width()), "height": int(preview.height())}
        if preview is not None and not preview.isNull()
        else None
    )
    return {
        "selectionId": selection.selection_id,
        "hasSelection": bool(selection.has_selection),
        "bounds": bounds,
        "maskDimensions": mask_dimensions,
        "previewBounds": selection.preview_bounds,
        "previewDimensions": preview_dimensions,
        "maskSummary": _selection_mask_summary(
            has_selection=bool(selection.has_selection),
            bounds=bounds,
            mask_dimensions=mask_dimensions,
            preview_bounds=selection.preview_bounds,
            preview_dimensions=preview_dimensions,
        ),
    }


def _selection_mask_summary(
    *,
    has_selection: bool,
    bounds: tuple[int, int, int, int] | None,
    mask_dimensions: Mapping[str, Any] | None,
    preview_bounds: tuple[int, int, int, int] | None,
    preview_dimensions: Mapping[str, Any] | None,
) -> dict[str, Any]:
    coverage = None
    if bounds is not None and isinstance(mask_dimensions, Mapping):
        mask_width = _safe_positive_int(mask_dimensions.get("width"))
        mask_height = _safe_positive_int(mask_dimensions.get("height"))
        if mask_width and mask_height:
            coverage = round((bounds[2] * bounds[3]) / (mask_width * mask_height), 6)
    summary = "none"
    if has_selection and bounds is not None:
        summary = "selection_bounds_metadata_only"
    return {
        "hasSelection": has_selection,
        "summary": summary,
        "bounds": bounds,
        "maskDimensions": scrub_secrets(dict(mask_dimensions or {})) or None,
        "coverageApprox": coverage,
        "preview": {
            "available": preview_bounds is not None or preview_dimensions is not None,
            "bounds": preview_bounds,
            "dimensions": scrub_secrets(dict(preview_dimensions or {})) or None,
        },
        "rawMaskIncluded": False,
    }


def _vector_context(scene: Any) -> dict[str, Any]:
    vector = scene.vector
    return {
        "previewLayerId": vector.preview_layer_id,
        "hasPreviewObject": vector.preview_object is not None,
        "selection": to_jsonable(vector.selection),
        "layers": [
            {
                "layerId": layer.layer_id,
                "vectorKind": layer.vector_kind,
                "vectorObjectCount": len(layer.vector_objects),
            }
            for layer in vector.layers
        ],
    }


def _history_context(canvas: Any) -> dict[str, Any]:
    if canvas is None:
        return {"available": False, "undoCount": None, "redoCount": None}
    undo_count = _safe_call_int(canvas, "history_undo_count")
    redo_count = _safe_call_int(canvas, "history_redo_count")
    return {
        "available": undo_count is not None or redo_count is not None,
        "undoCount": undo_count,
        "redoCount": redo_count,
        "recent": [],
    }


def _ai_readiness(service: Any) -> dict[str, Any]:
    external_allowed = bool(getattr(service.safety, "external_services_allowed", False))
    generation_capabilities = generation_submitter_capabilities(service)
    return {
        "externalServicesAllowed": external_allowed,
        "availableGateways": [],
        "canGenerateAssetOnly": bool(external_allowed and generation_capabilities["available"]),
        "canGenerateProductionCandidates": bool(
            external_allowed and generation_capabilities["productionCandidates"]
        ),
        "canPreviewOverlay": bool(getattr(service.safety, "roots", ())),
        "credentialReadPerformed": False,
    }


def _safety_context(service: Any) -> dict[str, Any]:
    return {
        "externalServicesAllowed": bool(
            getattr(service.safety, "external_services_allowed", False)
        ),
        "confirmationPolicy": str(getattr(service.safety, "confirmation_policy", "always_ask")),
        "directCanvasMutationAllowed": False,
        "credentialReadPerformed": False,
        "credentialValuesIncluded": False,
        "rawBinaryIncluded": False,
    }


def _role_hint_details(
    name: str,
    layer_type: str,
    vector_kind: str,
    index: int,
) -> list[dict[str, Any]]:
    lowered = unicodedata.normalize("NFKC", name).casefold()
    hints: list[dict[str, Any]] = []
    background_name = any(token in lowered for token in ("background", "bg", "背景"))
    if index == 0 or background_name:
        hints.append(
            {
                "role": "background",
                "confidence": 0.88 if background_name else 0.78,
                "reason": "name_match" if background_name else "bottom_layer_order",
            }
        )
    if layer_type in {"text"} or vector_kind == "text" or "text" in lowered or "文字" in lowered:
        hints.append({"role": "text", "confidence": 0.92, "reason": "text_layer_or_name"})
    if layer_type == "ai_effect" or "ai" in lowered:
        hints.append({"role": "ai_effect", "confidence": 0.86, "reason": "effect_layer_or_name"})
    if layer_type == "3d":
        hints.append({"role": "3d", "confidence": 0.9, "reason": "layer_type"})
    lineart_name = any(
        token in lowered for token in ("lineart", "line art", "線画", "inking", "ink")
    )
    if lineart_name:
        hints.append(
            {
                "role": "character_lineart",
                "confidence": 0.9,
                "reason": "name_match",
            }
        )
    if any(token in lowered for token in ("person", "character", "人物", "キャラ")):
        hints.append({"role": "character", "confidence": 0.84, "reason": "name_match"})
    if not hints:
        hints.append({"role": "paint", "confidence": 0.55, "reason": "fallback"})
    return hints


def _role_hints(name: str, layer_type: str, vector_kind: str, index: int) -> list[str]:
    return [
        str(detail.get("role"))
        for detail in _role_hint_details(name, layer_type, vector_kind, index)
        if detail.get("role")
    ]


def _style_memory_context(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return scrub_secrets(
        {
            "schemaVersion": STYLE_MEMORY_SCHEMA_VERSION,
            "storage": "runtime_local_only",
            "persistence": "none",
            "externalSync": False,
            "credentialReadPerformed": False,
            "entryCount": len(entries),
            "entries": [dict(entry) for entry in entries[-MAX_STYLE_MEMORY_ENTRIES:]],
        }
    )


def _style_memory_entries_from_apply(
    changeset: Mapping[str, Any],
    apply_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    now = _now()
    changeset_id = str(changeset.get("changesetId") or "")
    apply_id = str(apply_result.get("applyId") or "")
    entries: list[dict[str, Any]] = []
    for operation in _operation_list(changeset):
        op_name = str(operation.get("op") or operation.get("kind") or "")
        if op_name == "set_layer_opacity":
            entries.append(
                {
                    "entryId": f"style_pref_{uuid.uuid4().hex[:10]}",
                    "createdAt": now,
                    "kind": "layer_opacity_preference",
                    "value": float(operation.get("opacity") or 0.0),
                    "source": "applied_canvas_changeset",
                    "changesetId": changeset_id,
                    "applyId": apply_id,
                    "externalSync": False,
                }
            )
        elif op_name == "set_layer_blend_mode":
            entries.append(
                {
                    "entryId": f"style_pref_{uuid.uuid4().hex[:10]}",
                    "createdAt": now,
                    "kind": "blend_mode_preference",
                    "value": str(operation.get("blendMode") or "normal"),
                    "source": "applied_canvas_changeset",
                    "changesetId": changeset_id,
                    "applyId": apply_id,
                    "externalSync": False,
                }
            )
        elif op_name == "apply_selection_mask":
            entries.append(
                {
                    "entryId": f"style_pref_{uuid.uuid4().hex[:10]}",
                    "createdAt": now,
                    "kind": "selection_scope_preference",
                    "value": "selection_scoped_edit",
                    "source": "applied_canvas_changeset",
                    "changesetId": changeset_id,
                    "applyId": apply_id,
                    "externalSync": False,
                }
            )
    return entries


def _infer_intent(prompt: str) -> dict[str, Any]:
    normalized = unicodedata.normalize("NFKC", prompt).casefold()
    production = _production_recipe_for_prompt(normalized)
    if production is not None:
        return {
            "kind": "production_task",
            "confidence": 0.88,
            "summary": "Route high-level production orchestration to ProductionRecipe.",
            **production,
        }
    if _mentions_selection_reference(normalized) and not (
        _mentions_text(normalized) or _mentions_ai_generation(normalized)
    ):
        return {
            "kind": "selection_edit",
            "confidence": 0.74,
            "summary": "Preview or apply a local selection/mask scoped edit.",
        }
    if _mentions_text(normalized):
        intent = {
            "kind": "add_text",
            "confidence": 0.82,
            "summary": "Add editable text or title using a future changeset.",
        }
        requested_text = _requested_canvas_text(prompt)
        if requested_text:
            intent["text"] = requested_text
        if _mentions_selection_reference(normalized):
            intent["selectionMode"] = "active_selection"
        if "看板" in normalized:
            intent["objectHint"] = "sign"
        return intent
    if _mentions_ai_generation(normalized):
        return {
            "kind": "ai_generate",
            "confidence": 0.72,
            "summary": "Plan an AI-assisted generation step without starting it.",
        }
    if _mentions_background_or_style(normalized):
        return {
            "kind": "style_adjust",
            "confidence": 0.76,
            "summary": "Plan a non-destructive visual adjustment.",
        }
    return {
        "kind": "inspect",
        "confidence": 0.65,
        "summary": "Inspect the current canvas context.",
    }


def _plan_steps(intent: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    active_layer = context.get("activeLayer")
    target_layer_id = active_layer.get("layerId") if isinstance(active_layer, Mapping) else None
    steps = [
        {
            "id": "step_001",
            "kind": "observe",
            "title": "Read Canvas Agent edit context",
            "tool": "loomstroke_get_edit_context",
            "readOnly": True,
            "documentMutation": "none",
        }
    ]
    kind = str(intent.get("kind") or "inspect")
    if kind == "production_task":
        steps.append(
            {
                "id": "step_002",
                "kind": "handoff",
                "title": "Create a ProductionRecipe dry-run plan",
                "tool": "loomstroke_plan_production_job",
                "recipeId": intent.get("recipeId"),
                "readOnly": True,
                "documentMutation": "none",
            }
        )
        return steps
    if kind != "inspect":
        steps.append(
            {
                "id": "step_002",
                "kind": "propose_changeset",
                "title": "Draft a future CanvasChangeset",
                "targetLayerId": target_layer_id,
                "readOnly": True,
                "documentMutation": "none",
                "futureOperation": kind,
                "requiresApprovalBeforeApply": True,
            }
        )
    return steps


def _plan_target(context: Mapping[str, Any]) -> dict[str, Any]:
    document = context.get("document") if isinstance(context.get("document"), Mapping) else {}
    page = context.get("page") if isinstance(context.get("page"), Mapping) else {}
    active_layer = (
        context.get("activeLayer") if isinstance(context.get("activeLayer"), Mapping) else {}
    )
    selection = context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
    return {
        "documentId": document.get("documentId"),
        "pageId": page.get("pageId"),
        "layerId": active_layer.get("layerId"),
        "hasSelection": bool(selection.get("hasSelection")),
    }


def _handoff_card(
    intent: Mapping[str, Any],
    prompt: str,
    context: Mapping[str, Any],
) -> dict[str, Any] | None:
    if str(intent.get("kind") or "") != "production_task":
        return None
    target = _plan_target(context)
    recipe_id = str(intent.get("recipeId") or "")
    tool = str(intent.get("tool") or "loomstroke_plan_production_job")
    return scrub_secrets(
        {
            "schemaVersion": "canvas_agent_handoff.v1",
            "target": "production_recipe_runner",
            "reason": "high_level_production_task_outside_canvas_changeset_scope",
            "recipeId": recipe_id,
            "tool": tool,
            "readOnlyPlanTool": "loomstroke_plan_production_job",
            "canonicalRunner": "loomstroke_run_production_recipe",
            "inputTemplate": {
                "recipeId": recipe_id,
                "mode": "dry_run",
                "input": {
                    "target": target,
                    "brief": {
                        "source": "canvas_agent_plan",
                        "sourcePromptDigest": _sha256(prompt),
                    },
                },
            },
            "nextActions": [
                {
                    "kind": "plan_production_recipe",
                    "tool": "loomstroke_plan_production_job",
                    "recipeId": recipe_id,
                }
            ],
            "canvasChangesetAllowed": False,
        }
    )


def _required_permissions(intent: Mapping[str, Any]) -> list[str]:
    kind = str(intent.get("kind") or "inspect")
    permissions = ["read_context"]
    if kind in {"add_text", "style_adjust", "selection_edit"}:
        permissions.append("future_local_mutation")
    if kind == "ai_generate":
        permissions.extend(["future_external_service", "future_credential_read"])
    if kind == "production_task":
        permissions.append("production_handoff")
    return permissions


def _plan_blocked_reasons(
    prompt: str,
    context: Mapping[str, Any],
    intent: Mapping[str, Any],
    service: Any,
) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    active_layer = (
        context.get("activeLayer") if isinstance(context.get("activeLayer"), Mapping) else {}
    )
    if intent.get("kind") != "inspect":
        if active_layer.get("locked"):
            blocked.append(
                {
                    "code": "target_layer_locked",
                    "message": "Active target layer is locked.",
                    "action": "Unlock the layer or select another layer.",
                }
            )
        if active_layer and not bool(active_layer.get("visible", True)):
            blocked.append(
                {
                    "code": "target_layer_hidden",
                    "message": "Active target layer is hidden.",
                    "action": "Show the layer or select another visible layer.",
                }
            )
    if intent.get("kind") == "ai_generate" and not bool(
        getattr(service.safety, "external_services_allowed", False)
    ):
        blocked.append(
            {
                "code": "privacy_denied_for_future_generation",
                "message": "External services are disabled, so generation cannot be queued.",
                "action": "Enable privacy/allow_external_services before generation.",
            }
        )
    target_role, protected_role = _semantic_roles(unicodedata.normalize("NFKC", prompt).casefold())
    if (
        target_role
        and intent.get("kind") != "production_task"
        and _best_role_confidence(context, target_role) < ROLE_CONFIDENCE_THRESHOLD
    ):
        selection = (
            context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
        )
        if not selection.get("hasSelection"):
            blocked.append(
                {
                    "code": "target_confidence_low",
                    "message": "The semantic target is not confident enough for mutation.",
                    "action": "Select the target area or choose a specific layer.",
                    "targetRole": target_role,
                }
            )
    if (
        protected_role
        and intent.get("kind") != "production_task"
        and _best_role_confidence(context, protected_role) < ROLE_CONFIDENCE_THRESHOLD
    ):
        selection = (
            context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
        )
        if not selection.get("hasSelection"):
            blocked.append(
                {
                    "code": "protected_target_confidence_low",
                    "message": "The protected semantic target is not confident enough.",
                    "action": "Choose the protected layer or proceed without one.",
                    "targetRole": target_role,
                    "protectedRole": protected_role,
                }
            )
    if _mentions_selection_reference(unicodedata.normalize("NFKC", prompt).casefold()):
        selection = (
            context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
        )
        if not selection.get("hasSelection"):
            blocked.append(
                {
                    "code": "selection_required",
                    "message": "The request references a selected area but no selection exists.",
                    "action": "Create a selection or clarify the target layer.",
                }
            )
    return blocked


def _clarification_questions(
    prompt: str,
    context: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> list[dict[str, Any]]:
    normalized = unicodedata.normalize("NFKC", prompt).casefold()
    questions: list[dict[str, Any]] = []
    selection = context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
    if _mentions_selection_reference(normalized) and not selection.get("hasSelection"):
        questions.append(
            {
                "id": "clarify_target_selection",
                "question": "Which layer or area should the edit target?",
                "reason": "selection_reference_without_selection",
            }
        )
    target_role, protected_role = _semantic_roles(normalized)
    if target_role and not selection.get("hasSelection"):
        confidence = _best_role_confidence(context, target_role)
        if confidence < ROLE_CONFIDENCE_THRESHOLD:
            questions.append(
                {
                    "id": "clarify_semantic_target",
                    "question": "Which layer or selection should be treated as the target?",
                    "reason": "semantic_target_low_confidence",
                    "targetRole": target_role,
                    "confidence": confidence,
                }
            )
    if protected_role and not selection.get("hasSelection"):
        confidence = _best_role_confidence(context, protected_role)
        if confidence < ROLE_CONFIDENCE_THRESHOLD:
            questions.append(
                {
                    "id": "clarify_protected_target",
                    "question": "Which layer should be protected from the edit?",
                    "reason": "protected_target_low_confidence",
                    "targetRole": target_role,
                    "protectedRole": protected_role,
                    "confidence": confidence,
                }
            )
    active_layer = (
        context.get("activeLayer") if isinstance(context.get("activeLayer"), Mapping) else {}
    )
    if intent.get("kind") != "inspect" and not active_layer:
        questions.append(
            {
                "id": "clarify_target_layer",
                "question": "Which layer should the edit target?",
                "reason": "missing_active_layer",
            }
        )
    return questions


def _target_resolution(
    prompt: str,
    context: Mapping[str, Any],
    intent: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = unicodedata.normalize("NFKC", prompt).casefold()
    selection = context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
    active = _active_layer_candidate(context)
    if _mentions_selection_reference(normalized) and not selection.get("hasSelection"):
        return scrub_secrets(
            {
                "status": "unresolved",
                "detectedTarget": None,
                "candidateTargets": [active] if active else [],
                "protectedTargets": [],
                "requiredScope": "selection_bounds",
                "missingRequirements": ["selection"],
                "confidence": 0.0,
                "reason": "missing_selection",
            }
        )

    target_role, protected_role = _semantic_roles(normalized)
    if target_role or protected_role:
        target_candidates = _role_candidates(context, target_role) if target_role else []
        protected_candidates = _role_candidates(context, protected_role) if protected_role else []
        if target_role and not target_candidates:
            target_candidates = [_missing_role_candidate(target_role)]
        if protected_role and not protected_candidates:
            protected_candidates = [_missing_role_candidate(protected_role)]
        target_confidence = _best_candidate_confidence(target_candidates)
        protected_confidence = _best_candidate_confidence(protected_candidates)
        missing: list[str] = []
        if target_role and target_confidence < ROLE_CONFIDENCE_THRESHOLD:
            missing.append("target_layer")
        if protected_role and protected_confidence < ROLE_CONFIDENCE_THRESHOLD:
            missing.append("protected_layer")
        detected = (
            target_candidates[0]
            if target_candidates and target_confidence >= ROLE_CONFIDENCE_THRESHOLD
            else None
        )
        confidence_values = [
            value
            for value in (target_confidence, protected_confidence if protected_role else None)
            if value is not None
        ]
        confidence = min(confidence_values) if confidence_values else 0.0
        return scrub_secrets(
            {
                "status": "unresolved" if missing else "candidate",
                "detectedTarget": detected,
                "candidateTargets": target_candidates,
                "protectedTargets": protected_candidates,
                "requiredScope": "layer",
                "missingRequirements": missing,
                "confidence": round(float(confidence), 3),
                "reason": (
                    "protected_target_low_confidence"
                    if protected_role and "protected_layer" in missing
                    else (
                        "semantic_target_low_confidence" if missing else "semantic_target_candidate"
                    )
                ),
                "targetRole": target_role,
                "protectedRole": protected_role,
            }
        )

    return scrub_secrets(
        {
            "status": "resolved" if intent.get("kind") == "inspect" else "candidate",
            "detectedTarget": active,
            "candidateTargets": [active] if active else [],
            "protectedTargets": [],
            "requiredScope": "layer",
            "missingRequirements": [],
            "confidence": float(active.get("confidence") or 0.0) if active else 0.0,
            "reason": "active_layer_target",
        }
    )


def _actionable_clarification(
    prompt: str,
    context: Mapping[str, Any],
    intent: Mapping[str, Any],
    target_resolution: Mapping[str, Any],
) -> dict[str, Any] | None:
    del intent
    normalized = unicodedata.normalize("NFKC", prompt).casefold()
    reason = str(target_resolution.get("reason") or "")
    if reason == "missing_selection":
        return scrub_secrets(
            {
                "schemaVersion": CLARIFICATION_SCHEMA_VERSION,
                "clarificationId": f"clar_{uuid.uuid4().hex[:12]}",
                "kind": "missing_selection",
                "question": "No selection is active. Where should this edit be placed?",
                "reason": reason,
                "targetState": dict(target_resolution),
                "options": [
                    {
                        "optionId": "active_layer_canvas_center",
                        "label": "Use active layer center",
                        "description": "Place an editable object on the active layer area.",
                        "action": "patch_plan",
                        "patch": {
                            "targetLayerId": "$active",
                            "placement": "canvas_center",
                            "selectionMode": "none",
                            "resolveBlockedReasonCodes": ["selection_required"],
                        },
                    },
                    {
                        "optionId": "active_layer_canvas_top",
                        "label": "Use active layer top",
                        "description": "Place an editable object near the canvas top.",
                        "action": "patch_plan",
                        "patch": {
                            "targetLayerId": "$active",
                            "placement": "canvas_top",
                            "selectionMode": "none",
                            "resolveBlockedReasonCodes": ["selection_required"],
                        },
                    },
                    {
                        "optionId": "create_selection_first",
                        "label": "Create selection first",
                        "description": (
                            "Return to the canvas and create a selection before preview."
                        ),
                        "action": "ui_action",
                        "userAction": {"kind": "create_selection"},
                    },
                    _discard_clarification_option(),
                ],
            }
        )

    target_role, protected_role = _semantic_roles(normalized)
    if (
        target_role
        or protected_role
        or reason
        in {
            "semantic_target_low_confidence",
            "protected_target_low_confidence",
        }
    ):
        active = _active_layer_candidate(context)
        options = _semantic_clarification_options(
            context,
            target_resolution,
            target_role=target_role or "background",
            protected_role=protected_role,
            active=active,
        )
        return scrub_secrets(
            {
                "schemaVersion": CLARIFICATION_SCHEMA_VERSION,
                "clarificationId": f"clar_{uuid.uuid4().hex[:12]}",
                "kind": "low_confidence_semantic_target",
                "question": "Which layer is the target, and which layer should be protected?",
                "reason": reason or "semantic_target_low_confidence",
                "targetState": dict(target_resolution),
                "options": [*options, _discard_clarification_option()],
            }
        )
    return None


def _semantic_clarification_options(
    context: Mapping[str, Any],
    target_resolution: Mapping[str, Any],
    *,
    target_role: str,
    protected_role: str | None,
    active: Mapping[str, Any],
) -> list[dict[str, Any]]:
    target_candidates = _candidate_layers(target_resolution.get("candidateTargets"))
    protected_candidates = _candidate_layers(target_resolution.get("protectedTargets"))
    if not target_candidates and active:
        target_candidates = [active]

    options: list[dict[str, Any]] = []
    protected_ids = [str(candidate.get("layerId") or "") for candidate in protected_candidates[:1]]
    protected_ids = [layer_id for layer_id in protected_ids if layer_id]
    protected_label = _candidate_label(protected_candidates[0]) if protected_candidates else ""
    active_id = str(active.get("layerId") or "")
    for index, target in enumerate(target_candidates[:3], start=1):
        target_id = str(target.get("layerId") or "")
        if not target_id:
            continue
        target_label = _candidate_label(target)
        option_id = (
            "use_active_layer_as_background"
            if target_id == active_id and target_role == "background"
            else f"use_{target_role}_candidate_{index}"
        )
        description = f"Apply the non-destructive adjustment to {target_label}."
        label = f"Use {target_label}"
        if protected_ids:
            label = f"{label}; protect {protected_label}"
            description = (
                f"Apply the non-destructive adjustment to {target_label} while keeping "
                f"{protected_label} as a protected layer."
            )
        options.append(
            _semantic_patch_option(
                option_id=option_id,
                label=label,
                description=description,
                target_layer_id=target_id,
                target_role=target_role,
                protected_layer_ids=protected_ids,
                protected_role=protected_role,
                allow_missing_protected_target=not bool(protected_ids),
            )
        )

    if protected_role:
        fallback_target = target_candidates[0] if target_candidates else active
        fallback_target_id = str(fallback_target.get("layerId") or "")
        if fallback_target_id:
            fallback_target_label = _candidate_label(fallback_target)
            options.append(
                _semantic_patch_option(
                    option_id="proceed_without_lineart",
                    label=f"Use {fallback_target_label} without lineart protection",
                    description=(
                        "Apply the non-destructive adjustment without selecting a protected "
                        "lineart layer."
                    ),
                    target_layer_id=fallback_target_id,
                    target_role=target_role,
                    protected_layer_ids=[],
                    protected_role=protected_role,
                    allow_missing_protected_target=True,
                )
            )
    return _dedupe_options(options)


def _semantic_patch_option(
    *,
    option_id: str,
    label: str,
    description: str,
    target_layer_id: str,
    target_role: str,
    protected_layer_ids: Sequence[str],
    protected_role: str | None,
    allow_missing_protected_target: bool,
) -> dict[str, Any]:
    return {
        "optionId": option_id,
        "label": label,
        "description": description,
        "action": "patch_plan",
        "patch": {
            "targetLayerId": target_layer_id,
            "targetRole": target_role,
            "protectedLayerIds": list(protected_layer_ids),
            "protectedRole": protected_role,
            "allowMissingProtectedTarget": bool(allow_missing_protected_target),
            "resolveBlockedReasonCodes": [
                "target_confidence_low",
                "protected_target_confidence_low",
            ],
        },
    }


def _candidate_layers(value: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for candidate in _safe_sequence(value):
        if not isinstance(candidate, Mapping):
            continue
        if not str(candidate.get("layerId") or "").strip():
            continue
        candidates.append(dict(candidate))
    return candidates


def _candidate_label(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("name") or candidate.get("layerId") or "layer")


def _dedupe_options(options: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for option in options:
        option_id = str(option.get("optionId") or "")
        if not option_id or option_id in seen:
            continue
        seen.add(option_id)
        deduped.append(dict(option))
    return deduped


def _discard_clarification_option() -> dict[str, Any]:
    return {
        "optionId": "discard",
        "label": "Discard",
        "description": "Discard this run without preview or apply.",
        "action": "discard",
    }


def _patch_plan_for_clarification(
    plan: Mapping[str, Any],
    context: Mapping[str, Any],
    clarification: Mapping[str, Any],
    option: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    patch = option.get("patch") if isinstance(option.get("patch"), Mapping) else {}
    target_layer_id = _resolved_layer_id(
        payload.get("targetLayerId") or patch.get("targetLayerId"),
        context,
    )
    if not target_layer_id:
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Clarification answer did not resolve a target layer.",
            details={
                "clarificationId": clarification.get("clarificationId"),
                "selectedOptionId": option.get("optionId"),
                "code": "target_layer_required",
            },
        )
    protected_layer_ids = _resolved_layer_ids(
        (
            payload.get("protectedLayerIds")
            if "protectedLayerIds" in payload
            else patch.get("protectedLayerIds")
        ),
        context,
    )
    placement = str(payload.get("placement") or patch.get("placement") or "").strip()
    selection_mode = str(patch.get("selectionMode") or "").strip()
    resolve_codes = {
        str(code) for code in _safe_sequence(patch.get("resolveBlockedReasonCodes")) if str(code)
    }

    updated = dict(plan)
    intent = dict(plan.get("intent") if isinstance(plan.get("intent"), Mapping) else {})
    if placement:
        intent["placement"] = placement
    if selection_mode:
        intent["selectionMode"] = selection_mode
    if patch.get("targetRole"):
        intent["targetRole"] = patch.get("targetRole")
    if patch.get("protectedRole"):
        intent["protectedRole"] = patch.get("protectedRole")
    if "allowMissingProtectedTarget" in patch:
        intent["allowMissingProtectedTarget"] = bool(patch.get("allowMissingProtectedTarget"))
    intent["protectedLayerIds"] = protected_layer_ids
    updated["intent"] = scrub_secrets(intent)

    target = dict(plan.get("target") if isinstance(plan.get("target"), Mapping) else {})
    target["layerId"] = target_layer_id
    target["hasSelection"] = False if selection_mode == "none" else bool(target.get("hasSelection"))
    updated["target"] = scrub_secrets(target)

    blocked_reasons = []
    for reason in _safe_sequence(plan.get("blockedReasons")):
        if not isinstance(reason, Mapping):
            continue
        if str(reason.get("code") or "") not in resolve_codes:
            blocked_reasons.append(dict(reason))
    updated["blockedReasons"] = scrub_secrets(blocked_reasons)
    updated["clarificationQuestions"] = []
    updated["status"] = "planned" if not blocked_reasons else "blocked"
    target_resolution = dict(
        plan.get("targetResolution") if isinstance(plan.get("targetResolution"), Mapping) else {}
    )
    target_resolution.update(
        {
            "status": "resolved",
            "detectedTarget": _layer_candidate_by_id(context, target_layer_id),
            "protectedTargets": [
                _layer_candidate_by_id(context, layer_id) for layer_id in protected_layer_ids
            ],
            "missingRequirements": [],
            "reason": "clarification_answer",
        }
    )
    updated["targetResolution"] = scrub_secrets(target_resolution)
    clarification_payload = dict(clarification)
    clarification_payload.update(
        {
            "status": "resolved",
            "selectedOptionId": option.get("optionId"),
            "payloadDigest": _digest(
                {
                    "targetLayerId": target_layer_id,
                    "protectedLayerIds": protected_layer_ids,
                    "placement": placement,
                }
            ),
        }
    )
    updated["clarification"] = scrub_secrets(clarification_payload)
    updated["resolvedClarification"] = scrub_secrets(
        {
            "schemaVersion": CLARIFICATION_ANSWER_RESULT_SCHEMA_VERSION,
            "clarificationId": clarification.get("clarificationId"),
            "selectedOptionId": option.get("optionId"),
            "targetLayerId": target_layer_id,
            "protectedLayerIds": protected_layer_ids,
            "placement": placement or None,
            "documentMutation": "none",
            "historyMutation": "none",
            "assetWrite": "none",
        }
    )
    return scrub_secrets(updated)


def _clarification_option(
    clarification: Mapping[str, Any],
    selected_option_id: str,
) -> dict[str, Any]:
    for option in _safe_sequence(clarification.get("options")):
        if not isinstance(option, Mapping):
            continue
        if str(option.get("optionId") or "") == selected_option_id:
            return dict(option)
    raise McpStructuredError(
        ReasonCode.NOT_FOUND,
        "Canvas Agent clarification option was not found.",
        details={
            "clarificationId": clarification.get("clarificationId"),
            "selectedOptionId": selected_option_id,
        },
    )


def _clarification_answer_safety() -> dict[str, Any]:
    return {
        "externalPlannerStarted": False,
        "externalGenerationStarted": False,
        "credentialReadPerformed": False,
        "networkTaskStarted": False,
        "documentMutation": "none",
        "historyMutation": "none",
        "assetWrite": "none",
        "rawPromptIncluded": False,
        "rawBinaryIncluded": False,
    }


def _required_argument_text(args: Mapping[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            f"'{key}' is required.",
            code=-32602,
            details={"field": key},
        )
    return value.strip()


def _semantic_roles(prompt: str) -> tuple[str | None, str | None]:
    protected_role = None
    if any(token in prompt for token in ("線画", "lineart", "line art")):
        protected_role = "character_lineart"
    if any(token in prompt for token in ("背景だけ", "background only")):
        return "background", protected_role
    return _semantic_target_role(prompt), protected_role


def _active_layer_candidate(context: Mapping[str, Any]) -> dict[str, Any]:
    active = context.get("activeLayer") if isinstance(context.get("activeLayer"), Mapping) else {}
    layer_id = str(active.get("layerId") or "")
    if not layer_id:
        return {}
    return _layer_candidate_from_context(active, "active_layer", 1.0, "active_layer")


def _role_candidates(context: Mapping[str, Any], role: str | None) -> list[dict[str, Any]]:
    if not role:
        return []
    candidates: list[dict[str, Any]] = []
    for layer in _context_layers(context):
        best = _best_role_detail(layer, role)
        if best is None:
            continue
        candidates.append(
            _layer_candidate_from_context(
                layer,
                role,
                float(best.get("confidence") or 0.0),
                str(best.get("reason") or "role_hint"),
            )
        )
    candidates.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return candidates


def _layer_candidate_by_id(context: Mapping[str, Any], layer_id: str) -> dict[str, Any]:
    for layer in _context_layers(context):
        if str(layer.get("layerId") or "") == layer_id:
            role = str((layer.get("roleHints") or ["layer"])[0] or "layer")
            confidence = _best_candidate_confidence(
                [_active_layer_candidate({"activeLayer": layer})]
            )
            return _layer_candidate_from_context(layer, role, confidence or 1.0, "selected")
    return {"layerId": layer_id, "role": "layer", "confidence": 1.0, "reason": "selected"}


def _layer_candidate_from_context(
    layer: Mapping[str, Any],
    role: str,
    confidence: float,
    reason: str,
) -> dict[str, Any]:
    return scrub_secrets(
        {
            "layerId": layer.get("layerId"),
            "name": layer.get("name"),
            "role": role,
            "confidence": round(float(confidence), 3),
            "reason": reason,
            "visible": bool(layer.get("visible", True)),
            "locked": bool(layer.get("locked", False)),
        }
    )


def _missing_role_candidate(role: str) -> dict[str, Any]:
    return {
        "layerId": None,
        "name": None,
        "role": role,
        "confidence": 0.0,
        "reason": "no_confident_layer",
        "visible": None,
        "locked": None,
    }


def _best_role_detail(layer: Mapping[str, Any], role: str) -> Mapping[str, Any] | None:
    details = layer.get("roleHintDetails")
    if not isinstance(details, list):
        return None
    accepted_roles = {role}
    if role == "character_lineart":
        accepted_roles.add("character")
    best: Mapping[str, Any] | None = None
    best_confidence = 0.0
    for detail in details:
        if not isinstance(detail, Mapping):
            continue
        if str(detail.get("role") or "") not in accepted_roles:
            continue
        try:
            confidence = float(detail.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence >= best_confidence:
            best = detail
            best_confidence = confidence
    return best


def _best_candidate_confidence(candidates: Sequence[Mapping[str, Any]]) -> float:
    best = 0.0
    for candidate in candidates:
        try:
            best = max(best, float(candidate.get("confidence") or 0.0))
        except (TypeError, ValueError):
            continue
    return round(best, 3)


def _resolved_layer_id(value: Any, context: Mapping[str, Any]) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "$active":
        active = _active_layer_candidate(context)
        text = str(active.get("layerId") or "").strip()
    if not text:
        return None
    layer_ids = {str(layer.get("layerId") or "") for layer in _context_layers(context)}
    if text not in layer_ids:
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Clarification answer referenced a missing target layer.",
            details={"layerId": text, "code": "missing_target_layer"},
        )
    return text


def _resolved_layer_ids(value: Any, context: Mapping[str, Any]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        return []
    resolved: list[str] = []
    for item in values:
        layer_id = _resolved_layer_id(item, context)
        if layer_id and layer_id not in resolved:
            resolved.append(layer_id)
    return resolved


def _context_layers(context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    layers = context.get("layers")
    if not isinstance(layers, list):
        return []
    return [layer for layer in layers if isinstance(layer, Mapping)]


def _safe_sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _required_prompt(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'prompt' is required.",
            code=-32602,
            details={"field": "prompt"},
        )
    prompt = value.strip()
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'prompt' is too long.",
            code=-32602,
            details={"maxLength": MAX_PROMPT_LENGTH},
        )
    return prompt


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_list(getter: Any) -> list[dict[str, Any]]:
    try:
        value = getter()
    except Exception:
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _candidate_asset_ids(args: Mapping[str, Any], job: Mapping[str, Any]) -> list[str]:
    raw_asset_ids = args.get("assetIds")
    if not isinstance(raw_asset_ids, Sequence) or isinstance(raw_asset_ids, (str, bytes)):
        raw_asset_ids = job.get("resultAssetIds")
    if not isinstance(raw_asset_ids, Sequence) or isinstance(raw_asset_ids, (str, bytes)):
        return []
    asset_ids: list[str] = []
    for item in raw_asset_ids:
        asset_id = str(item or "").strip()
        if asset_id:
            asset_ids.append(asset_id)
    return list(dict.fromkeys(asset_ids))


def _validate_candidate_job(job: Mapping[str, Any]) -> None:
    status = str(job.get("status") or "").strip()
    if status != "completed":
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Candidate stack requires a completed generation job.",
            details={"jobId": job.get("jobId"), "status": status},
        )
    metadata = job.get("metadata") if isinstance(job.get("metadata"), Mapping) else {}
    submission = (
        metadata.get("submission") if isinstance(metadata.get("submission"), Mapping) else {}
    )
    if submission.get("applyToCanvas") is not False:
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Candidate stack requires an asset-only generation job.",
            details={
                "jobId": job.get("jobId"),
                "code": "candidate_job_must_be_asset_only",
            },
        )


def _validate_candidate_asset_id_for_job(asset_id: str, job: Mapping[str, Any]) -> None:
    result_asset_ids = {
        str(item or "").strip() for item in job.get("resultAssetIds", []) if str(item or "").strip()
    }
    if asset_id not in result_asset_ids:
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Candidate asset must belong to the originating generation job.",
            details={
                "jobId": job.get("jobId"),
                "assetId": asset_id,
                "code": "candidate_asset_not_in_job",
            },
        )


def _validate_candidate_asset_for_job(asset: Mapping[str, Any], job: Mapping[str, Any]) -> None:
    asset_id = str(asset.get("assetId") or "").strip()
    kind = str(asset.get("kind") or "").strip()
    source = str(asset.get("source") or "").strip()
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), Mapping) else {}
    job_id = str(job.get("jobId") or "").strip()
    if kind != "generated_output" or source != "generation":
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Candidate asset must be a generated output asset.",
            details={
                "jobId": job_id,
                "assetId": asset_id,
                "kind": kind,
                "source": source,
                "code": "candidate_asset_not_generation_output",
            },
        )
    if str(metadata.get("jobId") or "").strip() != job_id:
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Candidate asset metadata does not match the generation job.",
            details={
                "jobId": job_id,
                "assetId": asset_id,
                "code": "candidate_asset_job_mismatch",
            },
        )


def _operation_list(changeset: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    operations = changeset.get("operations")
    if not isinstance(operations, list):
        return []
    return [operation for operation in operations if isinstance(operation, Mapping)]


def _history_label(changeset: Mapping[str, Any]) -> str:
    changeset_id = str(changeset.get("changesetId") or "canvas_changeset")
    return f"AI: {changeset_id}"


def _first_live_or_first(items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in items:
        if item.get("source") == "live":
            return item
    return items[0] if items else {}


def _active_or_first_page(
    pages: list[dict[str, Any]],
    document: Mapping[str, Any],
) -> dict[str, Any]:
    active_page_id = document.get("activePageId") or document.get("active_page_id")
    if active_page_id:
        for page in pages:
            if page.get("pageId") == active_page_id or page.get("page_id") == active_page_id:
                return page
    return pages[0] if pages else {}


def _safe_call_int(obj: Any, method_name: str) -> int | None:
    method = getattr(obj, method_name, None)
    if not callable(method):
        return None
    try:
        return int(method())
    except Exception:
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _planner_error_code(exc: McpStructuredError) -> str:
    details = exc.details if isinstance(exc.details, Mapping) else {}
    code = str(details.get("code") or "").strip()
    if code:
        if code.startswith("planner_output_"):
            return "planner_output_validation_failed"
        return code
    reason_value = getattr(exc.reason_code, "value", exc.reason_code)
    reason = str(reason_value or "").strip()
    return reason or "planner_provider_failed"


def _planner_sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _omit_prompt_text(value: Any, prompt: str) -> Any:  # noqa: PLR0911
    if not prompt:
        return value
    if isinstance(value, str):
        return value.replace(prompt, "<omitted_prompt>")
    if isinstance(value, Mapping):
        return {str(key): _omit_prompt_text(item, prompt) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_omit_prompt_text(item, prompt) for item in value]
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(repr(scrub_secrets(value)).encode("utf-8")).hexdigest()[:24]


def _mentions_text(prompt: str) -> bool:
    return any(
        token in prompt
        for token in ("title", "text", "caption", "label", "タイトル", "文字", "テキスト", "看板")
    )


def _mentions_ai_generation(prompt: str) -> bool:
    return any(
        token in prompt for token in ("ai", "generate", "comfy", "nanobanana", "gptimage", "生成")
    )


def _mentions_background_or_style(prompt: str) -> bool:
    return any(
        token in prompt
        for token in (
            "background",
            "bg",
            "evening",
            "sunset",
            "tone",
            "color",
            "背景",
            "夕方",
            "色",
            "雰囲気",
        )
    )


def _mentions_selection_reference(prompt: str) -> bool:
    return any(
        token in prompt
        for token in (
            "selection",
            "selected",
            "only this part",
            "this area",
            "ここ",
            "この範囲",
            "この部分",
            "範囲",
            "部分だけ",
        )
    )


def _requested_canvas_text(prompt: str) -> str | None:
    for start, end in (("「", "」"), ("『", "』"), ("“", "”")):
        start_index = prompt.find(start)
        if start_index < 0:
            continue
        end_index = prompt.find(end, start_index + len(start))
        if end_index <= start_index:
            continue
        candidate = prompt[start_index + len(start) : end_index].strip()
        if candidate:
            return candidate[:2000]
    for match in re.finditer(r"([\"'])(?P<text>.+?)\1", prompt):
        candidate = match.group("text").strip()
        if candidate:
            return candidate[:2000]
    return None


def _production_recipe_for_prompt(prompt: str) -> dict[str, Any] | None:
    if any(
        token in prompt for token in ("キャラデザイン", "キャラクターデザイン", "character design")
    ):
        return {
            "recipeId": "character.design.create",
            "domain": "character_design",
            "tool": "loomstroke_plan_production_job",
        }
    if any(
        token in prompt
        for token in (
            "ゲーム用3dアセット",
            "3dアセット",
            "3d asset",
            "game asset",
            "game-ready 3d",
        )
    ):
        return {
            "recipeId": "game_asset_3d.create",
            "domain": "game_asset_3d",
            "tool": "loomstroke_plan_production_job",
        }
    if any(
        token in prompt
        for token in (
            "漫画ページ全体",
            "マンガページ全体",
            "comic page",
            "manga page",
            "4コマ",
        )
    ):
        return {
            "recipeId": "comic_page.improve",
            "domain": "comic_page",
            "tool": "loomstroke_plan_production_job",
        }
    return None


def _semantic_target_role(prompt: str) -> str | None:
    if any(token in prompt for token in ("背景だけ", "background only")):
        return "background"
    if any(token in prompt for token in ("人物だけ", "人物", "キャラだけ", "キャラクターだけ")):
        return "character"
    if any(token in prompt for token in ("person only", "character only")):
        return "character"
    return None


def _best_role_confidence(context: Mapping[str, Any], role: str) -> float:
    best = 0.0
    layers = context.get("layers")
    if not isinstance(layers, list):
        return best
    for layer in layers:
        if not isinstance(layer, Mapping):
            continue
        details = layer.get("roleHintDetails")
        if not isinstance(details, list):
            continue
        for detail in details:
            if not isinstance(detail, Mapping) or detail.get("role") != role:
                continue
            try:
                confidence = float(detail.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            best = max(best, confidence)
    return round(best, 3)
