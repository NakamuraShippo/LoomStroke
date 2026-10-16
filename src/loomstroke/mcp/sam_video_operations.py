"""Track registered image sequences through a private native video session and export."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

from loomstroke.ai_selection.data_model import Sam3JobStatus
from loomstroke.ai_selection.video_package_writer import write_video_mask_package_export
from loomstroke.ai_selection.video_tracker_model import Sam3VideoTrackResult
from loomstroke.sam31 import worker_protocol as protocol

from .editing_common import edit_operation, finite_number, invalid
from .extension_services import require_service
from .sam_asset_common import asset_error, load, provider_store
from .sam_video_artifacts import materialize_video, prepare_frames, save_source_sequence
from .types import McpStructuredError, ReasonCode


def _track(manager: Any, config: Any, directory: Path, count: int, text: str) -> dict[str, Any]:
    with manager.image_transaction():
        if manager.worker_state() != "running":
            manager.start(config)
        if manager.model_status().get("modelState") != protocol.MODEL_LOADED:
            manager.load_model()
        session = manager.video_request(
            protocol.CMD_VIDEO_SESSION_START, {"framesDir": str(directory / "frames")}
        )
        token = session["sessionToken"]
        operation_error = None
        try:
            manager.video_request(
                protocol.CMD_VIDEO_ADD_PROMPT,
                {"sessionToken": token, "frameIndex": 0, "prompt": text},
            )
            return manager.video_request(
                protocol.CMD_VIDEO_PROPAGATE,
                {"sessionToken": token, "outputDir": str(directory / "masks"), "maxFrames": count},
            )
        except Exception as exc:
            operation_error = exc
            raise
        finally:
            try:
                manager.video_request(protocol.CMD_VIDEO_SESSION_CLOSE, {"sessionToken": token})
            except Exception as close_error:
                if operation_error is None:
                    raise
                operation_error.add_note(
                    "Video session close failed: " + type(close_error).__name__
                )


async def _complete_tracking(
    store: Any,
    manager: Any,
    config: Any,
    track_id: str,
    ids: list[str],
    payloads: list[bytes],
    fps: float,
    text: str,
) -> dict[str, Any]:
    directory = store.root_path / "worker_tmp" / track_id
    output_ids: list[str] = []
    sequence: dict[str, Any] = {}
    try:
        (directory / "frames").mkdir(parents=True)
        (directory / "masks").mkdir()
        store.save_job_status(Sam3JobStatus(track_id, "track_video", "running"))
        size = await asyncio.to_thread(prepare_frames, payloads, directory / "frames")
        sequence = await asyncio.to_thread(
            save_source_sequence, store, track_id, ids, payloads, fps
        )
        result = await asyncio.to_thread(_track, manager, config, directory, len(ids), text)
        track = await asyncio.to_thread(
            materialize_video,
            store,
            result,
            directory / "masks",
            track_id,
            len(ids),
            size,
            fps,
            text,
            output_ids,
            sequence,
        )
        store.save_job_status(
            Sam3JobStatus(
                track_id,
                "track_video",
                "completed",
                progress=1,
                terminal=True,
                output_asset_ids=tuple(output_ids),
                diagnostics={"trackResult": track.to_dict(), "sourceSequence": sequence},
            )
        )
    except Exception as exc:
        store.save_job_status(
            Sam3JobStatus(
                track_id,
                "track_video",
                "failed",
                terminal=True,
                output_asset_ids=tuple(output_ids),
                diagnostics={
                    "backendCode": str(getattr(exc, "code", type(exc).__name__)),
                    "sourceSequence": sequence,
                },
            )
        )
        if isinstance(exc, McpStructuredError):
            raise
        raise asset_error(exc) from exc
    finally:
        if directory.resolve().is_relative_to(store.root_path.resolve()):
            await asyncio.to_thread(shutil.rmtree, directory, True)
    return {
        "status": "completed",
        "jobId": track_id,
        "frameCount": len(track.frames),
        "maskAssetIds": output_ids,
        "resourceUri": f"loomstroke://sam3/job/{track_id}",
        "sourceSnapshotIds": sequence["frameSnapshotIds"],
        "canvasChanged": False,
    }


async def _wait_for_native_completion(task: asyncio.Task) -> dict[str, Any]:
    """A cancelled caller cannot remove inputs still owned by a native worker."""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break  # _complete_tracking already recorded the terminal failure.
        if not task.cancelled() and (error := task.exception()) is not None:
            cancelled.add_note("Native tracking finished with " + type(error).__name__)
        raise


async def track_video(service: Any, name: str, args: Any) -> dict[str, Any]:
    _provider, store = provider_store(service)
    manager = require_service(service, "sam31_manager")
    config = require_service(service, "sam31_config")
    ids = args.get("frameAssetIds")
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 32
        or any(not isinstance(value, str) or not value or len(value) > 256 for value in ids)
    ):
        raise invalid(
            "'frameAssetIds' must contain 1-32 ordered registered image assets.", "frameAssetIds"
        )
    text = args.get("prompt")
    if not isinstance(text, str) or not text.strip() or len(text) > 2048:
        raise invalid("'prompt' must be non-empty text up to 2048 characters.", "prompt")
    fps = finite_number(args.get("fps", 24), "fps", minimum=0.01, maximum=240)
    payloads = []
    payload_bytes = 0
    for value in ids:
        if service._asset_record(value) is None:
            raise McpStructuredError(ReasonCode.NOT_FOUND, "A video frame asset is not registered.")
        payload = service._asset_bytes(value)
        payload_bytes += len(payload)
        if payload_bytes > 134_217_728:
            raise invalid("Video frame encodings exceed the 128 MB limit.", "frameAssetIds")
        payloads.append(payload)
    track_id = "sam31-video-" + uuid.uuid4().hex
    async with edit_operation(service, name, args, require_canvas=False):
        task = asyncio.create_task(
            _complete_tracking(store, manager, config, track_id, ids, payloads, fps, text.strip())
        )
        return await _wait_for_native_completion(task)


async def export_video(service: Any, name: str, args: Any) -> dict[str, Any]:
    _provider, store = provider_store(service)
    job = load(store, "job_status", args.get("jobId"), "jobId")
    if (
        job.kind != "track_video"
        or job.status != "completed"
        or not isinstance(job.diagnostics.get("trackResult"), dict)
    ):
        raise McpStructuredError(ReasonCode.CONFLICT, "The job has no completed video mask result.")
    track = Sam3VideoTrackResult.from_dict(job.diagnostics["trackResult"])
    raw = args.get("outputDirectory")
    if not isinstance(raw, str) or not raw:
        raise invalid(
            "'outputDirectory' must be a new directory within MCP roots.", "outputDirectory"
        )
    manifest = service.safety.validate_output_path(
        Path(raw) / "video_mask_package.json", allowed_suffixes=(".json",)
    )
    output = manifest.parent
    if output.exists():
        raise McpStructuredError(
            ReasonCode.CONFLICT, "Use a new directory for the video mask package."
        )
    payloads = {
        asset_id: store.mask_asset_path(
            load(store, "mask_asset", asset_id, "maskAssetIds")
        ).read_bytes()
        for asset_id in job.output_asset_ids
    }
    async with edit_operation(service, name, args, require_canvas=False):
        try:
            if output.exists():
                raise McpStructuredError(
                    ReasonCode.CONFLICT, "The output directory changed during confirmation."
                )
            written = await asyncio.to_thread(
                write_video_mask_package_export,
                track,
                output,
                payloads,
                allowed_roots=service.safety.roots,
                include_previews=False,
            )
        except (ValueError, OSError) as exc:
            raise asset_error(exc) from exc
    return {
        **written,
        "status": "completed",
        "outputDirectory": str(output),
        "canvasChanged": False,
    }
