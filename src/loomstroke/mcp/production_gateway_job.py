"""Complete Gateway jobs while keeping ComfyUI recipe outputs asset-only."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QImage

from loomstroke.plugins.ai_effect.gateway_protocol import JobResult, OutputImage
from loomstroke.plugins.ai_effect.gateways.comfyui import (
    _extract_model_outputs,
    _extract_outputs,
    _ServiceBackedJobHandle,
)

from .generation_stream import guarded_stream
from .types import McpStructuredError, ReasonCode


async def wait_asset_only_job(service: Any, handle: Any) -> tuple[JobResult, Any]:
    """Drain the real handle's progress before reading its terminal outputs.

    ComfyUI's normal wait() also creates 3D layers. Reuse its history parsers
    and connection without invoking that canvas-mutating UI path.
    """
    comfy = isinstance(handle, _ServiceBackedJobHandle)
    connection = handle._get_connection() if comfy else None
    progress = getattr(handle, "progress", None)
    if callable(progress):
        async for _ in guarded_stream(service, progress()):
            pass
    service.safety.require_external_services()
    if not comfy:
        return await handle.wait(), connection
    if handle._cancelled or handle._terminal_error is not None or handle._result_data is None:
        return await handle.wait(), connection
    if connection is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "ComfyUI output connection is unavailable."
        )
    parser_arguments = {
        "history": handle._result_data,
        "job_id": handle.job_id,
        "output_node_ids": handle._output_node_ids,
        "output_internal_ids": handle._output_internal_ids,
    }
    models = _extract_model_outputs(**parser_arguments)
    images = []
    for internal_id, files in _extract_outputs(**parser_arguments):
        for file in files:
            service.safety.require_external_services()
            data = await connection.get_image(
                file["filename"],
                subfolder=file.get("subfolder", ""),
                folder_type=file.get("type", "output"),
            )
            if len(data) > 64 * 1024 * 1024:
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED, "Generated image exceeds 64 MiB."
                )
            image = QImage.fromData(data)
            if image.isNull() or max(image.width(), image.height()) > 16384:
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED, "Generated image is invalid or too large."
                )
            images.append(OutputImage(internal_id, bytes(data), image.width(), image.height()))
    return (
        JobResult("completed" if images or models else "failed", images, 0.0, output_models=models),
        connection,
    )
