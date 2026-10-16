"""MCP prompt template handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .types import McpPromptArgument, McpPromptSpec, McpStructuredError, ReasonCode

PROMPTS: tuple[McpPromptSpec, ...] = (
    McpPromptSpec(
        name="loomstroke_create_with_mcp",
        description=(
            "Guide a client AI to complete natural-language creation requests through "
            "LoomStroke MCP one-shot tools."
        ),
        arguments=(
            McpPromptArgument(
                "request",
                "User's creation request, e.g. 'LoomStrokeMCPで4コマ漫画を作って'.",
                required=True,
            ),
        ),
    ),
    McpPromptSpec(
        name="loomstroke_inspect_document",
        description="Create a review prompt for document, page, layer, asset, and job structure.",
        arguments=(
            McpPromptArgument("document_id", "Document id to inspect. Defaults to current."),
            McpPromptArgument("focus", "Optional focus such as layers, assets, or jobs."),
        ),
    ),
    McpPromptSpec(
        name="loomstroke_plan_generation",
        description="Create a safe AI Effect generation planning prompt before tool execution.",
        arguments=(
            McpPromptArgument("layer_id", "AI Effect layer id to plan for.", required=True),
            McpPromptArgument("goal", "User's generation goal.", required=True),
        ),
    ),
    McpPromptSpec(
        name="loomstroke_debug_render_diff",
        description="Create a render-diff investigation prompt for wgpu native/bitmap findings.",
        arguments=(
            McpPromptArgument("page_id", "Page id or current."),
            McpPromptArgument("symptom", "Observed render difference or warning.", required=True),
        ),
    ),
)


def list_prompts() -> dict[str, Any]:
    return {"prompts": [prompt.to_protocol() for prompt in PROMPTS]}


def get_prompt(name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    args = dict(arguments or {})
    if name == "loomstroke_create_with_mcp":
        request = _required(args, "request")
        text = (
            "Complete this LoomStroke MCP creation request end to end: "
            f"{request}\n\n"
            "Read loomstroke://agent-guide before creating, especially comicWorkflow for comics. "
            "Use loomstroke_create first for non-comic requests unless the user explicitly named "
            "a lower-level tool, and pass the user's original request as request. "
            "For manga/comic/4-panel/comic-page requests, prepare a script with panel scenes "
            "and quoted dialogue, then pass script directly to loomstroke_create_comic_page. "
            "The generic router uses request as literal script and does not invent a story or "
            "forward a separate script/story argument. For artwork, editable pages, or export, "
            "follow the guide's staged workflow and actual readiness/confirmation requirements. "
            "Treat blocked=true as a successful partial "
            "completion, not a failed tool call; report the returned preview assetId and explain "
            "only the blocked write/backend step. Do not expose credentials, tokens, private "
            "endpoints, or raw binary payloads."
        )
        return _prompt_result(name, text)
    if name == "loomstroke_inspect_document":
        document_id = str(args.get("document_id") or "current")
        focus = str(args.get("focus") or "document/page/layer/resource consistency")
        text = (
            "Inspect the LoomStroke document structure via MCP resources. "
            f"Document id: {document_id}. Focus: {focus}. "
            "Check page ordering, layer metadata, asset references, job metadata, "
            "and compatibility guards. Do not request file writes or generation unless "
            "the user explicitly approves a tool call."
        )
        return _prompt_result(name, text)
    if name == "loomstroke_plan_generation":
        layer_id = _required(args, "layer_id")
        goal = _required(args, "goal")
        text = (
            "Plan a LoomStroke AI Effect generation before execution. "
            f"Target layer id: {layer_id}. Goal: {goal}. "
            "Confirm privacy gate, credential readiness, source slot layers, roots for any export, "
            "and user confirmation requirements before calling mutation or external-service tools."
        )
        return _prompt_result(name, text)
    if name == "loomstroke_debug_render_diff":
        page_id = str(args.get("page_id") or "current")
        symptom = _required(args, "symptom")
        text = (
            "Debug a LoomStroke render difference using MCP snapshots and preview metadata. "
            f"Page id: {page_id}. Symptom: {symptom}. "
            "Compare RenderSceneSnapshot layer order, texture_version, dirty rects, "
            "unsupported wgpu layer warnings, and native/bitmap presenter diagnostics."
        )
        return _prompt_result(name, text)
    raise McpStructuredError(
        ReasonCode.NOT_FOUND,
        "Prompt not found.",
        details={"prompt": name},
        code=-32601,
    )


def _prompt_result(name: str, text: str) -> dict[str, Any]:
    return {
        "description": name,
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": text,
                },
            }
        ],
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
