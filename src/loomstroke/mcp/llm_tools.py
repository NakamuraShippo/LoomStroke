"""Prompt, image, structured response, and streaming jobs via the existing LLM Gateway."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.plugins.llm_gateway.config import known_models, load, resolve_model
from loomstroke.plugins.sdk.base import JobSpec

from .editing_common import check_gui_thread, finite_number, invalid
from .extension_services import require_service
from .generation_stream import guarded_stream
from .safety import scrub_secrets
from .types import McpStructuredError, McpToolSpec, ReasonCode

_TEXT = {"type": "string", "minLength": 1}
_JOB = {"jobId": _TEXT}


def _tool(name, description, properties, required=(), *, read_only=False):
    return McpToolSpec(
        name,
        description,
        {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
        read_only=read_only,
        external=not read_only,
    )


TOOLS = (
    _tool(
        "loomstroke_list_llm_models",
        "List configured and bundled LLM model IDs without reading credentials.",
        {},
        read_only=True,
    ),
    _tool(
        "loomstroke_submit_llm",
        "Submit prompt/messages and rooted images to LLM Gateway; stream with status cursors.",
        {
            "prompt": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "array", "minItems": 1, "items": {"type": "object"}},
                ]
            },
            "provider": {"enum": ["openai", "anthropic", "gemini"]},
            "model": _TEXT,
            "system": {"type": "string"},
            "responseFormat": {"enum": ["text", "json"]},
            "jsonSchema": {"type": "object"},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            "maxTokens": {"type": "integer", "minimum": 1, "maximum": 1000000},
            "imagePaths": {"type": "array", "maxItems": 16, "items": _TEXT},
        },
        ["prompt"],
    ),
    _tool(
        "loomstroke_get_llm_status",
        "Poll streaming deltas since cursor without waiting for completion.",
        {**_JOB, "cursor": {"type": "integer", "minimum": 0}},
        ["jobId"],
        read_only=True,
    ),
    _tool(
        "loomstroke_get_llm_result",
        "Wait for the complete LLM response and return structured output.",
        {**_JOB, "timeoutMs": {"type": "integer", "minimum": 0, "maximum": 60000}},
        ["jobId"],
        read_only=True,
    ),
    _tool(
        "loomstroke_cancel_llm",
        "Cancel a pending LLM job through its provider and stop local streaming.",
        _JOB,
        ["jobId"],
    ),
)


@dataclass
class LlmJob:
    job_id: str
    provider_job_id: str
    gateway: Any
    status: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    task: asyncio.Task | None = None


async def _consume(service: Any, job: LlmJob) -> None:
    try:
        # Gateway.result() alone may consume only one chunk. Always drain stream first.
        service.safety.require_external_services()
        async for event in guarded_stream(service, job.gateway.stream(job.provider_job_id)):
            job.events.append(
                scrub_secrets(
                    {
                        "step": event.step,
                        "total": event.total,
                        "message": event.message,
                        "metadata": event.metadata,
                    }
                )
            )
        service.safety.require_external_services()
        job.result = scrub_secrets(asdict(await job.gateway.result(job.provider_job_id)))
        job.status = "completed" if job.result.get("success") is True else "failed"
    except asyncio.CancelledError:
        job.status = "cancelled"
        raise
    except Exception as exc:
        job.status = "failed"
        job.result = {
            "success": False,
            "error": "LLM Gateway failed.",
            "errorType": type(exc).__name__,
        }


def _job(service: Any, job_id: Any) -> LlmJob:
    jobs = getattr(service, "_mcp_llm_jobs", {})
    if not isinstance(job_id, str) or job_id not in jobs:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "LLM job not found.")
    return jobs[job_id]


def _snapshot(job: LlmJob) -> dict[str, Any]:
    return {"jobId": job.job_id, "status": job.status, "cursor": len(job.events)}


def _params(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    prompt = args.get("prompt")
    if not isinstance(prompt, str | list) or not prompt:
        raise invalid("'prompt' requires text or a non-empty message list.", "prompt")
    result = {"prompt": prompt}
    for source, target in (
        ("provider", "provider"),
        ("model", "model"),
        ("system", "system"),
        ("responseFormat", "response_format"),
        ("jsonSchema", "json_schema"),
    ):
        if source in args:
            result[target] = args[source]
    if "temperature" in args:
        result["temperature"] = finite_number(
            args["temperature"], "temperature", minimum=0, maximum=2
        )
    if "maxTokens" in args:
        maximum = finite_number(args["maxTokens"], "maxTokens", minimum=1, maximum=1000000)
        if not maximum.is_integer():
            raise invalid("'maxTokens' must be an integer.")
        result["max_tokens"] = int(maximum)
    images = []
    for raw_path in args.get("imagePaths", []):
        path = service.safety.validate_input_path(
            raw_path, allowed_suffixes=(".png", ".jpg", ".jpeg", ".webp")
        )
        if path.stat().st_size > 20 * 1024 * 1024:
            raise invalid("Image attachment exceeds 20 MiB.", "imagePaths")
        data = path.read_bytes()
        image = QImage.fromData(data)
        if image.isNull() or max(image.width(), image.height()) > 16384:
            raise invalid("Image attachment is invalid or too large.", "imagePaths")
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }[path.suffix.lower()]
        images.append(
            {
                "data": data,
                "width": image.width(),
                "height": image.height(),
                "mime": mime,
                "source": "file",
            }
        )
    if images:
        result["images"] = images
    return result


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    check_gui_thread()
    args = dict(arguments)
    if name == "loomstroke_list_llm_models":
        config = load(getattr(service.safety, "settings", getattr(service, "settings", None)))
        return {
            "defaultProvider": config.provider,
            "providers": [
                {
                    "provider": key,
                    "defaultModel": resolve_model(config, key, None),
                    "models": list(
                        dict.fromkeys([resolve_model(config, key, None), *known_models(key)])
                    ),
                }
                for key in ("openai", "anthropic", "gemini")
            ],
        }
    if name == "loomstroke_submit_llm":
        service.safety.require_external_services()
        params = _params(service, args)
        gateway = require_service(service, "llm_gateway")
        # Reuse the existing external queue operation policy, without including prompt/images.
        await service.safety.require_confirmation(
            "loomstroke_submit_generation", {"gateway": "llm", "provider": args.get("provider")}
        )
        service.safety.require_external_services()
        provider_id = await gateway.submit(JobSpec("prompt_completion", params))
        job = LlmJob(str(uuid.uuid4()), provider_id, gateway)
        jobs = getattr(service, "_mcp_llm_jobs", None)
        if jobs is None:
            jobs = service._mcp_llm_jobs = {}
        jobs[job.job_id] = job
        job.task = asyncio.create_task(_consume(service, job))
        return _snapshot(job)
    job = _job(service, args.get("jobId"))
    if name == "loomstroke_get_llm_status":
        cursor = finite_number(args.get("cursor", 0), "cursor", minimum=0)
        if not cursor.is_integer() or cursor > len(job.events):
            raise invalid("Invalid stream cursor.", "cursor")
        return {**_snapshot(job), "events": job.events[int(cursor) :]}
    if name == "loomstroke_get_llm_result":
        timeout = finite_number(args.get("timeoutMs", 60000), "timeoutMs", minimum=0, maximum=60000)
        if job.task is not None and not job.task.done() and timeout > 0:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(job.task), timeout / 1000)
        return {**_snapshot(job), "result": job.result}
    if name != "loomstroke_cancel_llm":
        raise invalid("Unknown LLM operation.")
    if job.status in {"completed", "failed", "cancelled"}:
        return {**_snapshot(job), "backendCancelled": False}
    service.safety.require_external_services()
    await service.safety.require_confirmation(name, {"jobId": job.job_id})
    service.safety.require_external_services()
    cancelled = bool(await job.gateway.cancel(job.provider_job_id))
    if job.task is not None and not job.task.done():
        job.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await job.task
    return {**_snapshot(job), "backendCancelled": cancelled}


async def close_jobs(service: Any) -> None:
    """Stop local stream consumers when this MCP service detaches; no external request."""
    tasks = []
    for job in getattr(service, "_mcp_llm_jobs", {}).values():
        if job.task is not None and not job.task.done():
            job.task.cancel()
            tasks.append(job.task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
