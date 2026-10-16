"""Bounded local SAM31 lifecycle tools; no hidden worker starts or path disclosure."""

from __future__ import annotations

import asyncio
from typing import Any

from loomstroke.sam31.server_manager import Sam31ServerError

from .editing_common import edit_operation, invalid
from .extension_services import require_service
from .sam_asset_common import schema
from .types import McpStructuredError, McpToolSpec, ReasonCode

_SAFE = {
    "status",
    "protocolVersion",
    "modelState",
    "alreadyLoaded",
    "alreadyUnloaded",
    "loadSeconds",
    "freedMb",
    "python",
    "platform",
    "torch",
    "cudaAvailable",
    "gpuName",
    "vramTotalMb",
    "torchAllocatedMb",
    "torchReservedMb",
}
_FEATURES = {
    "text",
    "point_new_object",
    "point_refine",
    "negative_point",
    "box",
    "exemplar",
    "refine",
    "video_tracking",
}
TOOLS = tuple(
    McpToolSpec("loomstroke_sam31_" + suffix, description, schema({}))
    for suffix, description in (
        ("health", "Read the local worker/model health without starting a stopped worker."),
        (
            "runtime_info",
            "Read path-free local runtime versions, GPU memory and capabilities "
            "without starting a stopped worker.",
        ),
        (
            "load_model",
            "Explicitly start the configured local SAM31 worker if needed and load its model. "
            "May consume GPU memory.",
        ),
        (
            "unload_model",
            "Explicitly unload the local SAM31 model and image/video sessions, freeing GPU memory. "
            "Keep the worker running.",
        ),
    )
)


def _safe(value: Any) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key in _SAFE and isinstance(item, str | int | float | bool | type(None))
    }


def _run(manager: Any, config: Any, kind: str) -> dict[str, Any]:
    with manager.image_transaction():
        state = manager.worker_state()
        result = {"workerState": state, "modelState": manager.last_model_state()}
        if state != "running" and kind != "load_model":
            return result
        if kind == "load_model":
            if state != "running":
                manager.start(config)
            result.update(_safe(manager.load_model()))
            result["workerState"] = manager.worker_state()
        elif kind == "unload_model":
            result.update(_safe(manager.unload_model()))
        elif kind == "health":
            result.update(_safe(manager.health()))
        else:
            result["runtime"] = _safe(manager.runtime_info())
            capabilities = manager.capabilities()
            result["modelState"] = capabilities.get("modelState", result["modelState"])
            result["capabilities"] = {
                key: value
                for key, value in capabilities.get("features", {}).items()
                if key in _FEATURES and value in {"native", "shim", "unavailable"}
            }
        return result


async def dispatch(service: Any, name: str, arguments: Any) -> dict[str, Any]:
    if name not in {tool.name for tool in TOOLS}:
        raise invalid("Unknown SAM31 runtime tool.")
    manager = require_service(service, "sam31_manager")
    kind = name.removeprefix("loomstroke_sam31_")
    config = require_service(service, "sam31_config") if kind == "load_model" else None
    try:
        if kind in {"load_model", "unload_model"}:
            async with edit_operation(service, name, arguments, require_canvas=False):
                result = await asyncio.to_thread(_run, manager, config, kind)
        else:
            result = await asyncio.to_thread(_run, manager, config, kind)
    except Sam31ServerError as exc:
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED,
            "The local SAM31 worker request failed.",
            details={"backendCode": exc.code},
        ) from exc
    return {"status": "completed", **result, "canvasChanged": False}
