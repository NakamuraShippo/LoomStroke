"""Rule-based graphic planning schema for LoomStroke MCP."""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .safety import scrub_secrets
from .types import McpStructuredError, ReasonCode

PLAN_TTL_SECONDS = 10 * 60
MAX_PROMPT_LENGTH = 8_000
MAX_CLIENT_REQUEST_ID_LENGTH = 128

Permission = Literal[
    "read_context",
    "local_non_destructive",
    "local_mutation",
    "destructive_mutation",
    "file_write",
    "external_service",
    "credential_read",
    "external_planner",
    "reserved_3d",
]
StepKind = Literal[
    "read_context",
    "canvas_command",
    "page_op",
    "layer_import",
    "layer_transform",
    "text_add",
    "ai_generate",
    "gallery_record",
    "export",
    "save_document",
    "ask_user",
    "reserved_3d",
]
RiskLevel = Literal["none", "low", "medium", "high", "destructive"]
PrimaryRisk = Literal[
    "none",
    "local_mutation",
    "destructive",
    "external",
    "file_write",
    "credential",
    "reserved_3d",
]


@dataclass(frozen=True, slots=True)
class GraphicContextRef:
    document_id: str | None
    page_id: str | None
    active_layer_id: str | None
    selected_layer_ids: tuple[str, ...] = ()
    document_version: str | None = None
    page_version: str | None = None
    layer_version_digest: str | None = None

    def to_schema(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "pageId": self.page_id,
            "activeLayerId": self.active_layer_id,
            "selectedLayerIds": list(self.selected_layer_ids),
            "documentVersion": self.document_version,
            "pageVersion": self.page_version,
            "layerVersionDigest": self.layer_version_digest,
        }


@dataclass(frozen=True, slots=True)
class GraphicBlockedReason:
    code: str
    message: str
    step_id: str | None = None
    permission: str | None = None

    def to_schema(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "stepId": self.step_id,
            "permission": self.permission,
        }


@dataclass(frozen=True, slots=True)
class OutputDescriptor:
    kind: str
    description: str
    target: dict[str, Any] = field(default_factory=dict)

    def to_schema(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "target": dict(self.target),
        }


@dataclass(frozen=True, slots=True)
class GraphicStep:
    id: str
    kind: StepKind
    title: str
    payload: Mapping[str, Any]
    required_permissions: frozenset[Permission] = field(default_factory=frozenset)
    risk_level: RiskLevel = "none"
    primary_risk: PrimaryRisk = "none"
    undo_group: str | None = None
    requires_confirmation: bool = False
    stale_policy: Literal["fail", "replan", "ask_user"] = "fail"

    def to_schema(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "payload": scrub_secrets(dict(self.payload)),
            "requiredPermissions": sorted(self.required_permissions),
            "riskLevel": self.risk_level,
            "primaryRisk": self.primary_risk,
            "undoGroup": self.undo_group,
            "requiresConfirmation": self.requires_confirmation,
            "stalePolicy": self.stale_policy,
        }


@dataclass(frozen=True, slots=True)
class GraphicPlan:
    plan_id: str
    created_at: str
    expires_at: str
    summary: str
    context: GraphicContextRef
    steps: tuple[GraphicStep, ...]
    requires_confirmation: bool
    required_permissions: frozenset[Permission]
    blocked_reasons: tuple[GraphicBlockedReason, ...] = ()
    estimated_outputs: tuple[OutputDescriptor, ...] = ()
    source_prompt_digest: str | None = None

    def to_schema(self) -> dict[str, Any]:
        return {
            "planId": self.plan_id,
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "summary": self.summary,
            "context": self.context.to_schema(),
            "steps": [step.to_schema() for step in self.steps],
            "requiresConfirmation": self.requires_confirmation,
            "requiredPermissions": sorted(self.required_permissions),
            "blockedReasons": [reason.to_schema() for reason in self.blocked_reasons],
            "estimatedOutputs": [output.to_schema() for output in self.estimated_outputs],
            "sourcePromptDigest": self.source_prompt_digest,
        }


class GraphicPlanBuilder:
    """Small local planner that never calls an external model."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def build(self, arguments: Mapping[str, Any]) -> GraphicPlan:
        prompt = _required_prompt(arguments.get("prompt"))
        client_request_id = arguments.get("clientRequestId")
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

        prompt_digest = _digest(prompt)
        target = _target_ref(arguments.get("target"))
        context = self._context_ref()
        steps = self._build_steps(prompt, prompt_digest, target)
        blocked_reasons = self._blocked_reasons(steps)
        required_permissions = frozenset(
            permission for step in steps for permission in step.required_permissions
        )
        requires_confirmation = any(step.requires_confirmation for step in steps)
        now = datetime.now(UTC)
        return GraphicPlan(
            plan_id=f"gplan_{uuid.uuid4().hex[:12]}",
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=PLAN_TTL_SECONDS)).isoformat(),
            summary=self._summary(steps),
            context=context,
            steps=tuple(steps),
            requires_confirmation=requires_confirmation,
            required_permissions=required_permissions or frozenset({"read_context"}),
            blocked_reasons=tuple(blocked_reasons),
            estimated_outputs=tuple(self._estimated_outputs(steps)),
            source_prompt_digest=prompt_digest,
        )

    def _context_ref(self) -> GraphicContextRef:
        documents = _safe_list(self._service.list_documents)
        document = _first_live_or_first(documents)
        document_id = _str_or_none(document.get("documentId") or document.get("document_id"))
        page_id = _str_or_none(document.get("activePageId") or document.get("active_page_id"))

        scene = _safe_mapping(lambda: self._service.get_scene_snapshot())
        active_layer_id = _str_or_none(scene.get("activeLayerId"))
        canvas = scene.get("canvas") if isinstance(scene.get("canvas"), Mapping) else {}
        canvas_version = _str_or_none(canvas.get("layerCacheVersion"))
        layers = scene.get("layers") if isinstance(scene.get("layers"), list) else []
        layer_digest = _layer_version_digest(layers)

        if page_id is None:
            pages = _safe_list(self._service.list_pages)
            first_page = pages[0] if pages else {}
            page_id = _str_or_none(first_page.get("pageId") or first_page.get("page_id"))

        return GraphicContextRef(
            document_id=document_id,
            page_id=page_id,
            active_layer_id=active_layer_id,
            document_version=canvas_version,
            page_version=canvas_version,
            layer_version_digest=layer_digest,
        )

    def _build_steps(
        self,
        prompt: str,
        prompt_digest: str,
        target: dict[str, str],
    ) -> list[GraphicStep]:
        lowered = unicodedata.normalize("NFKC", prompt).casefold()
        steps = [
            GraphicStep(
                id="step_001",
                kind="read_context",
                title="Inspect current LoomStroke context",
                payload={"promptDigest": prompt_digest, "target": target},
                required_permissions=frozenset({"read_context"}),
            )
        ]

        if _mentions_3d(lowered):
            steps.append(
                self._step(
                    steps,
                    "reserved_3d",
                    "Plan reserved 3D layer operation",
                    {"target": target, "status": "requires_future_schema"},
                    {"reserved_3d"},
                    risk_level="medium",
                    primary_risk="reserved_3d",
                    requires_confirmation=True,
                )
            )
        if _mentions_ai(lowered):
            steps.append(
                self._step(
                    steps,
                    "ai_generate",
                    "Plan AI generation",
                    {"target": target, "promptDigest": prompt_digest},
                    {"external_service", "credential_read", "local_mutation"},
                    risk_level="high",
                    primary_risk="external",
                    requires_confirmation=True,
                )
            )
        if _mentions_text(lowered):
            steps.append(
                self._step(
                    steps,
                    "text_add",
                    "Plan text layer creation",
                    {"target": target},
                    {"local_mutation"},
                    risk_level="low",
                    primary_risk="local_mutation",
                    requires_confirmation=True,
                    undo_group="mcp_text_add",
                )
            )
        if _mentions_layer_mutation(lowered):
            steps.append(
                self._step(
                    steps,
                    "canvas_command",
                    "Plan layer command",
                    {"target": target},
                    {"local_mutation"},
                    risk_level="low",
                    primary_risk="local_mutation",
                    requires_confirmation=True,
                    undo_group="mcp_layer_command",
                )
            )
        if _mentions_export(lowered):
            steps.append(
                self._step(
                    steps,
                    "export",
                    "Plan image export",
                    {"target": target},
                    {"file_write"},
                    risk_level="medium",
                    primary_risk="file_write",
                    requires_confirmation=True,
                )
            )
        if _mentions_save(lowered):
            steps.append(
                self._step(
                    steps,
                    "save_document",
                    "Plan document save",
                    {"target": target},
                    {"file_write"},
                    risk_level="high",
                    primary_risk="file_write",
                    requires_confirmation=True,
                )
            )
        return steps

    def _step(
        self,
        existing: list[GraphicStep],
        kind: StepKind,
        title: str,
        payload: Mapping[str, Any],
        permissions: set[Permission],
        *,
        risk_level: RiskLevel,
        primary_risk: PrimaryRisk,
        requires_confirmation: bool,
        undo_group: str | None = None,
    ) -> GraphicStep:
        return GraphicStep(
            id=f"step_{len(existing) + 1:03d}",
            kind=kind,
            title=title,
            payload=payload,
            required_permissions=frozenset(permissions),
            risk_level=risk_level,
            primary_risk=primary_risk,
            requires_confirmation=requires_confirmation,
            undo_group=undo_group,
        )

    def _blocked_reasons(self, steps: list[GraphicStep]) -> list[GraphicBlockedReason]:
        blocked: list[GraphicBlockedReason] = []
        if any("reserved_3d" in step.required_permissions for step in steps):
            blocked.append(
                GraphicBlockedReason(
                    code="requires_future_schema",
                    message="3D graphic execution is reserved until the 3D layer schema lands.",
                    permission="reserved_3d",
                )
            )
        if (
            any("external_service" in step.required_permissions for step in steps)
            and not self._service.safety.external_services_allowed
        ):
            blocked.append(
                GraphicBlockedReason(
                    code="privacy_denied",
                    message="External AI services are disabled in LoomStroke privacy settings.",
                    permission="external_service",
                )
            )
        if (
            any("file_write" in step.required_permissions for step in steps)
            and not self._service.safety.roots
        ):
            blocked.append(
                GraphicBlockedReason(
                    code="roots_required",
                    message="File-writing steps require at least one allowed MCP root.",
                    permission="file_write",
                )
            )
        return blocked

    @staticmethod
    def _summary(steps: list[GraphicStep]) -> str:
        if len(steps) == 1:
            return "Inspect the current LoomStroke document context."
        action_titles = [step.title for step in steps if step.kind != "read_context"]
        return "Plan: " + "; ".join(action_titles)

    @staticmethod
    def _estimated_outputs(steps: list[GraphicStep]) -> list[OutputDescriptor]:
        outputs: list[OutputDescriptor] = []
        if any(step.kind == "ai_generate" for step in steps):
            outputs.append(OutputDescriptor("layer", "Generated layer or asset candidate"))
        if any(step.kind == "export" for step in steps):
            outputs.append(OutputDescriptor("file", "Exported image file under an allowed root"))
        if any(step.kind == "reserved_3d" for step in steps):
            outputs.append(OutputDescriptor("reserved_3d", "Reserved 3D layer plan"))
        return outputs


def _required_prompt(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'prompt' is required.",
            code=-32602,
            details={"argument": "prompt"},
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


def _target_ref(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    allowed_keys = ("documentId", "pageId", "layerId", "assetId")
    return {key: str(value[key]) for key in allowed_keys if value.get(key)}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_list(getter: Any) -> list[dict[str, Any]]:
    try:
        value = getter()
    except Exception:
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _safe_mapping(getter: Any) -> dict[str, Any]:
    try:
        value = getter()
    except Exception:
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _first_live_or_first(items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in items:
        if item.get("source") == "live":
            return item
    return items[0] if items else {}


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _layer_version_digest(layers: list[Any]) -> str | None:
    parts: list[str] = []
    for layer in layers:
        if not isinstance(layer, Mapping):
            continue
        layer_id = layer.get("id") or layer.get("layerId")
        texture_version = layer.get("textureVersion", 0)
        transform_version = layer.get("transformVersion", 0)
        parts.append(f"{layer_id}:{texture_version}:{transform_version}")
    if not parts:
        return None
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _mentions_3d(prompt: str) -> bool:
    return any(token in prompt for token in ("3d", "mesh", "model", "メッシュ", "モデル", "立体"))


def _mentions_ai(prompt: str) -> bool:
    return any(
        token in prompt
        for token in (
            "ai",
            "comfy",
            "nanobanana",
            "gptimage2",
            "gpt image",
            "openai",
            "inpaint",
            "img2img",
            "画像生成",
            "生成",
            "補完",
        )
    )


def _mentions_text(prompt: str) -> bool:
    return any(token in prompt for token in ("text", "caption", "label", "文字", "テキスト"))


def _mentions_layer_mutation(prompt: str) -> bool:
    has_layer = "layer" in prompt or "レイヤー" in prompt
    has_action = any(
        token in prompt for token in ("new", "add", "create", "duplicate", "追加", "作成", "複製")
    )
    return has_layer and has_action


def _mentions_export(prompt: str) -> bool:
    return any(token in prompt for token in ("export", "png", "write out", "書き出", "出力"))


def _mentions_save(prompt: str) -> bool:
    return any(token in prompt for token in ("save", "保存"))


__all__ = [
    "GraphicBlockedReason",
    "GraphicContextRef",
    "GraphicPlan",
    "GraphicPlanBuilder",
    "GraphicStep",
    "OutputDescriptor",
]
