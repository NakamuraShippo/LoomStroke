"""Read-only planner provider contract for Canvas Agent."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Protocol

from .canvas_changeset import (
    ALLOWED_OPERATIONS,
    FORBIDDEN_OPERATIONS,
    build_canvas_changeset,
    build_canvas_changeset_preview,
    validate_canvas_changeset,
)
from .safety import scrub_secrets
from .types import McpStructuredError, ReasonCode

PLAN_SCHEMA_VERSION = "canvas_edit_plan.v1"

_READ_ONLY_PLANNER_ALLOWLIST: dict[str, dict[str, Any]] = {
    "loomstroke_get_edit_context": {"mode": "read_only"},
    "loomstroke_list_ai_effect_presets": {"mode": "read_only"},
    "loomstroke_get_ai_effect_catalog": {"mode": "read_only"},
    "loomstroke_plan_canvas_edit": {"mode": "read_only"},
    "loomstroke_create_canvas_changeset": {"mode": "dry_run_required"},
    "loomstroke_preview_canvas_changeset": {"mode": "local_mode_required"},
}

_DENIED_PLANNER_TOOLS = frozenset(
    {
        "loomstroke_apply_canvas_changeset",
        "loomstroke_submit_generation",
    }
)

_PUBLIC_PLAN_KEYS = frozenset(
    {
        "blockedReasons",
        "clarificationQuestions",
        "handoff",
        "intent",
        "requiredPermissions",
        "requiredTools",
        "steps",
        "target",
    }
)

_PAYLOAD_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "binary",
        "bytes",
        "credential",
        "data",
        "fullresolutionimage",
        "image",
        "mask",
        "pngbytes",
        "prompt",
        "providerrequestbody",
        "rawbinary",
        "rawmask",
        "requestbody",
        "sessiontoken",
        "token",
    }
)


class CanvasAgentPlannerProvider(Protocol):
    """Provider interface for optional read-only external Canvas Agent planners."""

    def plan(
        self,
        prompt: str,
        context: Mapping[str, Any],
        tools: CanvasAgentReadOnlyPlannerTools,
    ) -> Mapping[str, Any]:
        """Return a CanvasEditPlan candidate."""


class CanvasAgentReadOnlyPlannerTools:
    """Tool policy wrapper exposed to read-only external planners."""

    def __init__(self, service: Any) -> None:
        self._service = service

    @property
    def allowlist_hash(self) -> str:
        return read_only_tool_allowlist_hash()

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": name, **dict(policy)}
            for name, policy in sorted(_READ_ONLY_PLANNER_ALLOWLIST.items())
        ]

    def ensure_allowed(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> None:
        args = dict(arguments or {})
        if name in _DENIED_PLANNER_TOOLS:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Canvas Agent planner cannot call side-effecting tools.",
                details={"tool": name, "plannerToolPolicy": "denied"},
            )
        policy = _READ_ONLY_PLANNER_ALLOWLIST.get(name)
        if policy is None:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Canvas Agent planner tool is not in the read-only allowlist.",
                details={"tool": name, "plannerToolPolicy": "not_allowlisted"},
            )
        mode = str(policy.get("mode") or "")
        if mode == "dry_run_required" and not _dry_run_requested(args):
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Canvas Agent planner can create changesets only in dry-run mode.",
                details={"tool": name, "plannerToolPolicy": mode},
            )
        if mode == "local_mode_required" and not _local_preview_requested(args):
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Canvas Agent planner can preview changesets only in explicit local mode.",
                details={"tool": name, "plannerToolPolicy": mode},
            )

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        self.ensure_allowed(name, arguments)
        args = dict(arguments or {})
        if name == "loomstroke_get_edit_context":
            return strip_planner_payloads(self._service.get_edit_context(args))
        if name == "loomstroke_plan_canvas_edit":
            return strip_planner_payloads(
                self._service.plan_canvas_edit({"plannerProvider": "deterministic", **args})
            )
        if name == "loomstroke_list_ai_effect_presets":
            return {
                "schemaVersion": "canvas_agent_planner_ai_effect_presets.v1",
                "presets": [],
                "credentialReadPerformed": False,
            }
        if name == "loomstroke_get_ai_effect_catalog":
            return {
                "schemaVersion": "canvas_agent_planner_ai_effect_catalog.v1",
                "catalog": [],
                "credentialReadPerformed": False,
            }
        if name == "loomstroke_create_canvas_changeset":
            return strip_planner_payloads(self._create_changeset_dry_run(args))
        if name == "loomstroke_preview_canvas_changeset":
            return strip_planner_payloads(self._preview_changeset_dry_run(args))
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "Planner tool is allowlisted but not available through this in-process adapter.",
            details={"tool": name},
        )

    def _create_changeset_dry_run(self, args: Mapping[str, Any]) -> dict[str, Any]:
        plan = self._resolve_plan(args)
        context = self._resolve_context(args)
        changeset = build_canvas_changeset(
            plan,
            context,
            existing_asset_ids=self._existing_asset_ids(),
        )
        validation = validate_canvas_changeset(
            changeset,
            context,
            existing_asset_ids=self._existing_asset_ids(),
        )
        changeset["validation"] = validation
        changeset["plannerDryRun"] = True
        return scrub_secrets(changeset)

    def _preview_changeset_dry_run(self, args: Mapping[str, Any]) -> dict[str, Any]:
        changeset = self._resolve_changeset(args)
        context = self._resolve_context(args)
        validation = validate_canvas_changeset(
            changeset,
            context,
            existing_asset_ids=self._existing_asset_ids(),
        )
        preview = build_canvas_changeset_preview(
            changeset,
            context,
            validation=validation,
            issue_ticket=False,
        )
        preview["plannerDryRun"] = True
        return scrub_secrets(preview)

    def _resolve_plan(self, args: Mapping[str, Any]) -> Mapping[str, Any]:
        plan = args.get("plan")
        if isinstance(plan, Mapping):
            return strip_planner_payloads(plan)
        run_id = str(args.get("runId") or "").strip()
        if run_id:
            return strip_planner_payloads(self._service.get_canvas_agent_run_plan(run_id))
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "Planner dry-run changeset requires a plan or runId.",
            details={"arguments": ["plan", "runId"]},
        )

    def _resolve_changeset(self, args: Mapping[str, Any]) -> Mapping[str, Any]:
        changeset = args.get("changeset")
        if isinstance(changeset, Mapping):
            return strip_planner_payloads(changeset)
        changeset_id = str(args.get("changesetId") or "").strip()
        if changeset_id:
            return strip_planner_payloads(self._service.get_canvas_agent_changeset(changeset_id))
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "Planner dry-run preview requires a changeset or changesetId.",
            details={"arguments": ["changeset", "changesetId"]},
        )

    def _resolve_context(self, args: Mapping[str, Any]) -> Mapping[str, Any]:
        context = args.get("editContext") or args.get("context")
        if isinstance(context, Mapping):
            return strip_planner_payloads(context)
        return strip_planner_payloads(self._service.get_edit_context({"includeThumbnail": False}))

    def _existing_asset_ids(self) -> set[str]:
        return {
            str(asset.get("assetId") or "")
            for asset in self._service.list_assets()
            if str(asset.get("assetId") or "")
        }


def read_only_tool_allowlist_hash() -> str:
    payload = {
        "allow": _READ_ONLY_PLANNER_ALLOWLIST,
        "deny": sorted(_DENIED_PLANNER_TOOLS),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_canvas_edit_plan_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return public plan fields from an external planner candidate."""

    if not isinstance(candidate, Mapping):
        raise _invalid("planner_output_not_object")
    schema = candidate.get("schemaVersion")
    if schema not in {None, PLAN_SCHEMA_VERSION}:
        raise _invalid("planner_output_schema_mismatch")
    if candidate.get("readOnly") is not None and candidate.get("readOnly") is not True:
        raise _invalid("planner_output_not_read_only")
    if str(candidate.get("documentMutation") or "none") != "none":
        raise _invalid("planner_output_document_mutation")
    if candidate.get("mutationAllowed") is not None and bool(candidate.get("mutationAllowed")):
        raise _invalid("planner_output_mutation_allowed")
    required_tools = candidate.get("requiredTools")
    if isinstance(required_tools, list):
        for tool in required_tools:
            tool_name = str(tool)
            if tool_name in _DENIED_PLANNER_TOOLS:
                raise _invalid("planner_output_denied_tool")
            if tool_name not in _READ_ONLY_PLANNER_ALLOWLIST:
                raise _invalid("planner_output_tool_not_allowlisted")
    if _contains_forbidden_tool_or_operation(candidate):
        raise _invalid("planner_output_forbidden_tool_or_operation")
    intent = candidate.get("intent")
    if not isinstance(intent, Mapping) or not str(intent.get("kind") or "").strip():
        raise _invalid("planner_output_intent_required")
    return {
        key: strip_planner_payloads(value)
        for key, value in candidate.items()
        if key in _PUBLIC_PLAN_KEYS
    }


def strip_planner_payloads(value: Any) -> Any:  # noqa: PLR0911
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = key_text.replace("-", "").replace("_", "").casefold()
            if normalized_key in _PAYLOAD_KEYS:
                continue
            cleaned[key_text] = strip_planner_payloads(item)
        return scrub_secrets(cleaned)
    if isinstance(value, list | tuple | set | frozenset):
        return [strip_planner_payloads(item) for item in value]
    if isinstance(value, bytes | bytearray | memoryview):
        return {"payloadOmitted": True}
    return value


def _dry_run_requested(args: Mapping[str, Any]) -> bool:
    return bool(args.get("dryRun") or args.get("dry_run") or args.get("previewOnly"))


def _local_preview_requested(args: Mapping[str, Any]) -> bool:
    return bool(
        args.get("localMode")
        or args.get("localOnly")
        or str(args.get("mode") or "").lower() == "local"
    )


def _contains_forbidden_tool_or_operation(value: Any) -> bool:  # noqa: PLR0911
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            item_text = str(item) if isinstance(item, str) else ""
            if key_text == "tool":
                if item_text in _DENIED_PLANNER_TOOLS:
                    return True
                if item_text.startswith("loomstroke_") and item_text not in (
                    _READ_ONLY_PLANNER_ALLOWLIST
                ):
                    return True
            if key_text in {"op", "operation"} and (
                item_text in FORBIDDEN_OPERATIONS or item_text not in ALLOWED_OPERATIONS
            ):
                return True
            if _contains_forbidden_tool_or_operation(item):
                return True
        return False
    if isinstance(value, list | tuple | set | frozenset):
        return any(_contains_forbidden_tool_or_operation(item) for item in value)
    return False


def _invalid(code: str) -> McpStructuredError:
    return McpStructuredError(
        ReasonCode.INVALID_ARGUMENT,
        "Canvas Agent planner output failed validation.",
        details={"code": code},
        code=-32602,
    )


__all__ = [
    "CanvasAgentPlannerProvider",
    "CanvasAgentReadOnlyPlannerTools",
    "read_only_tool_allowlist_hash",
    "strip_planner_payloads",
    "validate_canvas_edit_plan_candidate",
]
