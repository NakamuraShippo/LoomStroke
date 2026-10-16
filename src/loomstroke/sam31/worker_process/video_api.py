"""Native video sessions with compatible summaries and optional bounded mask PNGs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loomstroke.sam31 import worker_protocol as protocol

from .runtime import Sam31WorkerRuntime, WorkerError


def _session_id(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> str:
    token = request.get("sessionToken")
    session_id = runtime.video_sessions.get(str(token)) if token else None
    if session_id is None:
        raise WorkerError(protocol.ERR_SESSION_UNKNOWN, f"unknown session: {token!r}")
    return session_id


def video_session_start(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    frames_dir = request.get("framesDir")
    if not frames_dir or not Path(frames_dir).is_dir():
        raise WorkerError(
            protocol.ERR_INVALID_REQUEST, f"framesDir is not a directory: {frames_dir!r}"
        )
    predictor = runtime.require_predictor()
    response = predictor.handle_request({"type": "start_session", "resource_path": str(frames_dir)})
    token = runtime.new_video_token(response["session_id"])
    return {"sessionToken": token}


def _summarize(outputs: dict[str, Any]) -> dict[str, Any]:
    import numpy as np  # noqa: PLC0415

    masks = np.asarray(outputs.get("out_binary_masks"))
    object_ids = [int(v) for v in np.asarray(outputs.get("out_obj_ids")).reshape(-1)]
    areas = masks.reshape(masks.shape[0], -1).sum(axis=1).astype(int).tolist() if masks.size else []
    return {"objectIds": object_ids, "areasPx": areas}


def video_add_prompt(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    session_id = _session_id(runtime, request)
    prompt: dict[str, Any] = {
        "type": "add_prompt",
        "session_id": session_id,
        "frame_index": int(request.get("frameIndex", 0)),
    }
    if request.get("prompt"):
        prompt["text"] = str(request["prompt"])
    if request.get("points"):
        prompt["points"] = [[float(x), float(y)] for x, y in request["points"]]
        prompt["point_labels"] = [int(v) for v in request.get("labels", [])]
        if request.get("objectId") is not None:
            prompt["obj_id"] = int(request["objectId"])
    predictor = runtime.require_predictor()
    response = predictor.handle_request(prompt)
    return _summarize(response["outputs"])


def video_propagate(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    from .image_api import _validated_output_dir  # noqa: PLC0415
    from .video_masks import MAX_VIDEO_MASK_PIXELS, persist_frame  # noqa: PLC0415

    session_id = _session_id(runtime, request)
    predictor = runtime.require_predictor()
    directory = _validated_output_dir(request) if request.get("outputDir") else None
    limit = request.get("maxFrames", 32)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
        raise WorkerError(
            protocol.ERR_INVALID_REQUEST, "maxFrames must be an integer from 1 to 32."
        )
    remaining = MAX_VIDEO_MASK_PIXELS
    frames: dict[int, dict[str, Any]] = {}
    for response in predictor.handle_stream_request(
        {"type": "propagate_in_video", "session_id": session_id}
    ):
        index = int(response["frame_index"])
        if directory is not None:
            if len(frames) >= limit or index < 0 or index >= limit or index in frames:
                raise WorkerError(
                    protocol.ERR_INVALID_REQUEST,
                    "Video propagation exceeded its frame limit or repeated a frame.",
                )
            persisted, pixels = persist_frame(runtime, response["outputs"], directory, remaining)
            remaining -= pixels
            frames[index] = {**_summarize(response["outputs"]), "objects": persisted["objects"]}
        else:
            frames[index] = _summarize(response["outputs"])
    return {
        "frameCount": len(frames),
        "frames": frames,
        "maskPersistence": "grayscale_coverage_png" if directory is not None else "not_requested",
    }


def video_refine(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    if request.get("objectId") is None or not request.get("points"):
        raise WorkerError(protocol.ERR_INVALID_REQUEST, "video_refine requires objectId and points")
    return video_add_prompt(runtime, request)


def video_session_close(runtime: Sam31WorkerRuntime, request: dict[str, Any]) -> dict[str, Any]:
    token = str(request.get("sessionToken"))
    if token not in runtime.video_sessions:
        raise WorkerError(protocol.ERR_SESSION_UNKNOWN, f"unknown session: {token!r}")
    runtime._close_video_session(token)
    return {"closed": True}


HANDLERS = {
    protocol.CMD_VIDEO_SESSION_START: video_session_start,
    protocol.CMD_VIDEO_ADD_PROMPT: video_add_prompt,
    protocol.CMD_VIDEO_PROPAGATE: video_propagate,
    protocol.CMD_VIDEO_REFINE: video_refine,
    protocol.CMD_VIDEO_SESSION_CLOSE: video_session_close,
}

__all__ = ["HANDLERS"]
