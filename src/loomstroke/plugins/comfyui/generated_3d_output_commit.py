"""Prepare ComfyUI model artifacts before adding new 3D layers to the canvas."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from loomstroke.core.imported_3d_layer import (
    add_generated_3d_layers_with_history,
    capture_generated_3d_layer_owner,
)
from loomstroke.host_api import LayerRef
from loomstroke.plugins.common.comfyui_history import _HistoryTerminalError, _raise_terminal_failure

from .job_connection import _job_connection, _JobConnection, _JobConnectionError
from .output_targets import CREATE_NEW_3D_LAYER_ID

logger = logging.getLogger(__name__)


async def prepare_generated_3d_layer(
    service: Any,
    receiver: Any,
    job_id: str,
    node_id: str,
    file_info: tuple[str, str, str],
    job: _JobConnection | None,
) -> dict[str, Any]:
    filename, subfolder, folder_type = file_info
    model_bytes = await _job_connection(service, job).get_image(
        filename, subfolder=subfolder, folder_type=folder_type
    )
    _raise_terminal_failure(job)
    model_path = await service._store_downloaded_model(job_id, node_id, filename, model_bytes)
    if job is None:
        await service._prepare_downloaded_model(
            model_path, source_subfolder=subfolder, folder_type=folder_type
        )
    else:
        job.require()
        await service._prepare_downloaded_model(
            model_path, source_subfolder=subfolder, folder_type=folder_type, _job=job
        )
    _raise_terminal_failure(job)
    received = receiver.receive_scene_response(
        "comfyui.3d",
        {
            "prompt_id": job_id,
            "model_file": str(model_path),
            "node_id": node_id,
            "metadata": {
                "filename": filename,
                "subfolder": subfolder,
                "folder_type": folder_type,
            },
        },
    )
    return {
        "scene": received.scene,
        "display_mode": "solid",
        "gen_3d": {
            "provider_id": "comfyui.3d",
            "job_id": job_id,
            "node_id": node_id,
            "asset_id": received.manifest.asset_id,
            "artifact": received.artifact_relative_path,
            "filename": filename,
        },
        "origin": {"type": "comfyui_3d_output"},
    }


async def try_commit_new_generated_3d_layers(
    service: Any,
    canvas: Any,
    receiver: Any,
    job_id: str,
    outputs: dict[str, Any],
    targets: dict[str, LayerRef],
    *,
    job: _JobConnection | None,
    target_is_current: Callable[[], bool] | None,
) -> int | None:
    canvas_is_current = capture_generated_3d_layer_owner(canvas)
    if canvas_is_current is None or any(
        layer.id != CREATE_NEW_3D_LAYER_ID for layer in targets.values()
    ):
        return None

    def current() -> bool:
        return (
            getattr(service.host_api, "canvas", None) is canvas
            and canvas_is_current()
            and (target_is_current is None or target_is_current())
            and canvas_is_current()
        )

    prepared = await prepare_generated_3d_layers(
        service, receiver, job_id, outputs, targets, job=job, current=current
    )
    try:
        return add_generated_3d_layers_with_history(canvas, prepared) if prepared else 0
    except Exception as exc:
        logger.warning("Generated 3D layers could not be applied (%s)", type(exc).__name__)
        return 0


async def prepare_generated_3d_layers(
    service: Any,
    receiver: Any,
    job_id: str,
    outputs: dict[str, Any],
    node_ids: Iterable[str],
    *,
    job: _JobConnection | None,
    current: Callable[[], bool],
) -> tuple[dict[str, Any], ...]:
    """Load one artifact per output node without editing the document."""
    prepared = []
    try:
        for node_id in node_ids:
            if not current():
                return ()
            _raise_terminal_failure(job)
            node_output = outputs.get(node_id) if isinstance(outputs, dict) else None
            for entry in service._extract_3d_model_entries(node_output):
                info = service._model_file_info(entry)
                if info is not None:
                    break
            else:
                logger.warning("No 3D model artifact found for node %s in job %s", node_id, job_id)
                return ()
            values = await prepare_generated_3d_layer(service, receiver, job_id, node_id, info, job)
            prepared.append({"name": service._default_3d_layer_name(info[0]), **values})
        _raise_terminal_failure(job)
        if job is not None:
            job.require()
        return tuple(prepared) if current() else ()
    except (_JobConnectionError, _HistoryTerminalError):
        raise
    except Exception as exc:
        logger.warning("Generated 3D layers could not be prepared (%s)", type(exc).__name__)
        return ()
