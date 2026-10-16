"""Fetch typed ComfyUI model outputs with a privacy check before every external read."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from loomstroke.core.assets_3d import load_scene

from .extension_services import require_service
from .three_d_tools import register_model_asset
from .types import McpStructuredError, ReasonCode


async def register_model_outputs(
    service: Any, handle: Any, outputs: list[Any], *, connection: Any = None
) -> list[dict]:
    backend = getattr(handle, "_service", None) or require_service(service, "comfyui_service")
    connection = connection or getattr(backend, "_connection", None)
    if connection is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "Model download connection is unavailable."
        )
    assets = []
    for output in outputs:
        suffix = Path(output.filename).suffix.lower()
        if suffix not in {".glb", ".gltf", ".obj"}:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "Unsupported generated model format."
            )
        service.safety.require_external_services()
        data = await connection.get_image(
            output.filename, subfolder=output.subfolder, folder_type=output.folder_type
        )
        if len(data) > 64 * 1024 * 1024:
            raise McpStructuredError(ReasonCode.BACKEND_FAILED, "Generated model exceeds 64 MiB.")
        path = await backend._store_downloaded_model(
            handle.job_id, output.node_id, output.filename, data
        )
        if suffix == ".gltf":
            raw = json.loads(data)
            for item in [*raw.get("buffers", []), *raw.get("images", [])]:
                uri = item.get("uri") if isinstance(item, dict) else None
                if not isinstance(uri, str) or uri.startswith("data:"):
                    continue
                filename, subfolder = backend._dependency_file_info(uri, output.subfolder)
                service.safety.require_external_services()
                dependent = await connection.get_image(
                    filename, subfolder=subfolder, folder_type=output.folder_type
                )
                if len(dependent) > 64 * 1024 * 1024:
                    raise McpStructuredError(
                        ReasonCode.BACKEND_FAILED, "Generated model dependency exceeds 64 MiB."
                    )
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                item["uri"] = f"data:{mime};base64," + base64.b64encode(dependent).decode("ascii")
            path.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
        load_scene(path)
        assets.append(
            register_model_asset(
                service,
                path,
                metadata={
                    "jobId": handle.job_id,
                    "outputId": output.internal_id,
                    "source": "generation",
                },
            )
        )
    return assets
